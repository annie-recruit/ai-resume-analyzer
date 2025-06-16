import os
import json
import requests
import logging
from flask import Flask, request, jsonify, make_response
from docx import Document
from openai import OpenAI
from notion_client import Client
import PyPDF2
import io
from datetime import datetime
from io import BytesIO
import fitz
import matplotlib
matplotlib.use('Agg')  # 반드시 plt import 전에 설정해야 함
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from wordcloud import WordCloud
import base64
import re
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import tempfile
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import math
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image
import hashlib
import pickle
# 스크래핑 라이브러리 추가
from bs4 import BeautifulSoup
import time
from urllib.parse import urljoin, quote
from collections import Counter
# Selenium 추가
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import threading

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# 한글 폰트 설정
font_path = 'C:/Windows/Fonts/malgun.ttf'  # 맑은 고딕 폰트 경로
font_prop = fm.FontProperties(fname=font_path)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

# API 키 및 토큰을 환경변수에서 가져오기
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "your-slack-bot-token-here")
GPT_API_KEY = os.getenv("GPT_API_KEY", "your-openai-api-key-here")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "your-notion-database-id-here")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "your-notion-token-here")
JD_STORAGE_FILE = "stored_jd.pkl"  # JD 데이터 저장 파일

# Notion 클라이언트 초기화
notion = Client(auth=NOTION_TOKEN)

# Slack WebClient 초기화
client = WebClient(token=SLACK_BOT_TOKEN)

# JD 데이터 저장/불러오기 함수들
def save_jd_data():
    """JD 데이터를 파일로 저장"""
    try:
        with open(JD_STORAGE_FILE, 'wb') as f:
            pickle.dump(stored_jd, f)
        logging.info(f"JD data saved to {JD_STORAGE_FILE}")
    except Exception as e:
        logging.error(f"Failed to save JD data: {str(e)}")

def load_jd_data():
    """파일에서 JD 데이터를 불러오기"""
    try:
        if os.path.exists(JD_STORAGE_FILE):
            with open(JD_STORAGE_FILE, 'rb') as f:
                data = pickle.load(f)
            logging.info(f"JD data loaded from {JD_STORAGE_FILE}")
            return data
        else:
            logging.info("No existing JD data file found")
            return {}
    except Exception as e:
        logging.error(f"Failed to load JD data: {str(e)}")
        return {}

# Global variables for PDF generation and JD storage
last_analysis_result = None
last_analysis_user_id = None
stored_jd = load_jd_data()  # 시작 시 저장된 JD 데이터 불러오기
processed_messages = set()  # 처리된 메시지 ID 캐시
user_last_message = {}  # 사용자별 마지막 메시지 추적: {user_id: (timestamp, message_hash)}

SLACK_HEADERS = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
    "Content-Type": "application/json"
}

app = Flask(__name__)

def get_message_hash(user_id, text, timestamp):
    """메시지의 고유 해시 생성"""
    content = f"{user_id}:{text[:100]}:{timestamp}"  # 사용자ID:텍스트100자:타임스탬프
    return hashlib.md5(content.encode()).hexdigest()

def send_dm(user_id, text, blocks=None, file_url=None):
    """ 슬랙 DM + 버튼 옵션 """
    payload = {
        "channel": user_id,
        "text": text,
    }
    
    if blocks:
        payload["blocks"] = blocks
        
    if file_url:
        # 사용자에게 등록된 JD 목록 확인
        user_jds = []
        if user_id in stored_jd:
            user_jds = [jd_name for jd_name in stored_jd[user_id].keys() 
                       if not jd_name.startswith("_")]
        
        if len(user_jds) == 0:
            # JD가 없는 경우 기본 버튼
            payload["attachments"] = [{
                "text": "아래 버튼을 눌러 분석을 시작할까요?",
                "fallback": "분석을 시작할까요?",
                "callback_id": "resume_analysis",
                "color": "#3AA3E3",
                "actions": [
                    {
                        "name": "analyze_resume",
                        "text": "분석해줘!",
                        "type": "button",
                        "value": file_url,
                        "style": "primary"
                    },
                    {
                        "name": "download_resume",
                        "text": "이력서 다운로드",
                        "type": "button",
                        "url": file_url,
                        "style": "default"
                    }
                ]
            }]
        elif len(user_jds) == 1:
            # JD가 1개인 경우 자동 선택
            jd_name = user_jds[0]
            payload["attachments"] = [{
                "text": f"등록된 JD: **{jd_name}**",
                "fallback": "분석을 시작할까요?",
                "callback_id": "resume_analysis",
                "color": "#3AA3E3",
                "actions": [
                    {
                        "name": "analyze_resume_with_jd",
                        "text": "분석해줘!",
                        "type": "button",
                        "value": f"{file_url}|{jd_name}",
                        "style": "primary"
                    },
                    {
                        "name": "download_resume",
                        "text": "이력서 다운로드",
                        "type": "button",
                        "url": file_url,
                        "style": "default"
                    }
                ]
            }]
        else:
            # JD가 여러개인 경우 드롭다운 선택
            jd_options = []
            for jd_name in user_jds:
                jd_options.append({
                    "text": jd_name,
                    "value": f"{file_url}|{jd_name}"
                })
            
            payload["attachments"] = [{
                "text": "분석할 JD를 선택해주세요:",
                "fallback": "분석을 시작할까요?",
                "callback_id": "resume_analysis",
                "color": "#3AA3E3",
                "actions": [
                    {
                        "name": "select_jd_for_analysis",
                        "text": "JD 선택 후 분석",
                        "type": "select",
                        "options": jd_options,
                        "style": "primary"
                    },
                    {
                        "name": "analyze_resume",
                        "text": "JD 없이 분석",
                        "type": "button",
                        "value": file_url,
                        "style": "default"
                    },
                    {
                        "name": "download_resume",
                        "text": "이력서 다운로드",
                        "type": "button",
                        "url": file_url,
                        "style": "default"
                    }
                ]
            }]
    
    res = requests.post(
        "https://slack.com/api/chat.postMessage", 
        headers=SLACK_HEADERS, 
        json=payload  # data=json.dumps(payload) 대신 json=payload 사용
    )
    print("DM 전송결과:", res.status_code, res.text)

def search_notion_db(query):
    """Notion DB에서 검색을 수행하는 함수"""
    try:
        # 검색 필터 설정
        filter_conditions = {
            "or": [
                {
                    "property": "성명",
                    "title": {
                        "contains": query
                    }
                },
                {
                    "property": "강점 Top3",
                    "rich_text": {
                        "contains": query
                    }
                },
                {
                    "property": "역량카드 요약",
                    "rich_text": {
                        "contains": query
                    }
                }
            ]
        }
        
        # Notion DB 검색 실행
        results = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter=filter_conditions
        )
        
        return results.get('results', [])
    except Exception as e:
        logging.error(f"Notion DB 검색 중 오류 발생: {str(e)}")
        return []

def create_search_result_blocks(notion_page):
    """검색 결과를 Slack 블록으로 변환"""
    try:
        if not notion_page or 'id' not in notion_page:
            logging.error("Invalid notion_page object or missing ID")
            return None
            
        blocks = []
        
        # 제목 섹션
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": notion_page.get('properties', {}).get('이름', {}).get('title', [{}])[0].get('text', {}).get('content', '제목 없음'),
                "emoji": True
            }
        })
        
        # 주요 섹션들 추가
        sections = ['기술스택', '경력기간', '주요업무']
        for section in sections:
            content = notion_page.get('properties', {}).get(section, {}).get('rich_text', [{}])[0].get('text', {}).get('content', '')
            if content:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{section}*\n{content}"
                    }
                })
        
        # Notion 페이지 링크 버튼 추가
        page_id = notion_page['id'].replace('-', '')
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "📚 Notion에서 자세히 보기",
                            "emoji": True
                        },
                        "url": f"https://www.notion.so/{page_id}",
                        "style": "primary"
                    }
                ]
            }
        ])
        
        return blocks
    except Exception as e:
        logging.error(f"검색 결과 블록 생성 중 오류 발생: {str(e)}")
        return None

@app.route("/slack/events", methods=["GET", "POST"])
def slack_events():
    # GET 요청 처리 (URL 검증용)
    if request.method == "GET":
        return "OK"
        
    # POST 요청 처리
    try:
        # Content-Type 확인 및 데이터 파싱
        content_type = request.headers.get('Content-Type', '')
        logging.info(f"Received request with Content-Type: {content_type}")
        
        if content_type.startswith('application/json'):
            data = request.get_json()
        elif content_type.startswith('application/x-www-form-urlencoded'):
            # form 데이터에서 payload 추출 및 JSON 파싱
            form_data = request.form
            if 'payload' in form_data:
                data = json.loads(form_data['payload'])
            else:
                data = {k: v for k, v in form_data.items()}
        else:
            logging.error(f"Unsupported Content-Type: {content_type}")
            return jsonify({"error": "Unsupported Content-Type"}), 400
            
        if not data:
            logging.error("Empty request data")
            return jsonify({"error": "Empty request"}), 400
        
        logging.info(f"Received event data: {json.dumps(data, indent=2)}")
        
        # Interactive Message 처리
        if data.get("type") == "interactive_message":
            return handle_interactive_message(data)
        
        # URL 검증 처리
        if "challenge" in data:
            logging.info(f"Handling URL verification: {data['challenge']}")
            return jsonify({
                "challenge": data["challenge"]
            })
        
        # 이벤트 타입 확인
        if "event" not in data:
            logging.error("No event in data")
            return jsonify({"error": "No event"}), 400
        
        event = data.get("event", {})
        event_type = event.get("type")
        logging.info(f"Processing event type: {event_type}")
        
        # 파일 공유 이벤트 처리
        if event_type == "file_shared":
            try:
                file_id = event["file_id"]
                user_id = event["user_id"]
                logging.info(f"File shared - file_id: {file_id}, user_id: {user_id}")

                # 파일 정보 가져오기
                info = requests.get(
                    f"https://slack.com/api/files.info?file={file_id}",
                    headers=SLACK_HEADERS
                ).json()
                
                if not info.get("ok"):
                    logging.error(f"Failed to get file info: {info.get('error')}")
                    return make_response("", 200)

                # 봇이 업로드한 파일은 무시
                uploader_id = info["file"].get("user")
                if uploader_id == "U08TYB64MD3":  # 봇 ID
                    logging.info("Ignoring file uploaded by resume-bot")
                    return make_response("", 200)

                file_url = info["file"]["url_private_download"]
                send_dm(user_id, ":page_facing_up: 새 이력서가 업로드되었습니다. 분석을 시작할까요?", file_url=file_url)
                
            except Exception as e:
                logging.error(f"Error processing file_shared event: {str(e)}")
                return make_response("", 200)
                
        # 메시지 이벤트 처리
        elif event_type == "message" and event.get("channel_type") == "im":
            try:
                # 메시지 중복 처리 방지
                message_id = event.get("ts")
                if message_id in processed_messages:
                    logging.info(f"Message {message_id} already processed, skipping")
                    return make_response("", 200)
                
                # 봇 메시지 무시 (강화된 필터링)
                if event.get("bot_id") or event.get("subtype") == "bot_message":
                    logging.info("Ignoring bot message")
                    return make_response("", 200)
                    
                user_id = event.get("user")
                
                # 봇 자신의 메시지 무시 (사용자 ID로 추가 확인)
                if user_id == "U08TYB64MD3":  # 봇의 사용자 ID
                    logging.info("Ignoring message from bot user")
                    return make_response("", 200)
                
                # 사용자 ID가 없는 경우 무시
                if not user_id:
                    logging.info("No user ID in message event")
                    return make_response("", 200)
                
                text = event.get("text", "").strip()
                timestamp = float(message_id)
                
                # 메시지 해시 생성
                message_hash = get_message_hash(user_id, text, message_id)
                
                # 사용자별 중복 메시지 체크 (더 강화된 로직)
                if user_id in user_last_message:
                    last_timestamp, last_hash = user_last_message[user_id]
                    # 같은 해시이거나 5초 이내 중복 메시지인 경우 무시
                    if message_hash == last_hash or (timestamp - last_timestamp < 5):
                        logging.info(f"Duplicate message from user {user_id}, skipping. Hash: {message_hash}")
                        return make_response("", 200)
                
                # 사용자별 마지막 메시지 업데이트
                user_last_message[user_id] = (timestamp, message_hash)
                
                # 메시지를 처리된 목록에 추가
                processed_messages.add(message_id)
                
                # 캐시 크기 제한 (최근 1000개 메시지만 유지)
                if len(processed_messages) > 1000:
                    # 가장 오래된 500개 제거
                    old_messages = list(processed_messages)[:500]
                    for old_msg in old_messages:
                        processed_messages.discard(old_msg)
                
                logging.info(f"Processing message - user_id: {user_id}, hash: {message_hash}, text: {text[:50]}...")
                
                # JD 등록 키워드 감지
                jd_registration_keywords = ["jd 등록", "JD 등록", "jd등록", "JD등록", "jd 등록하기", "JD 등록하기"]
                if any(keyword in text for keyword in jd_registration_keywords):
                    trigger_jd_registration(user_id)
                    return make_response("", 200)
                
                # JD 목록 조회 키워드 감지
                jd_list_keywords = ["jd 목록", "JD 목록", "등록된 jd", "등록된 JD", "jd리스트", "JD리스트", "jd 리스트", "JD 리스트"]
                if any(keyword in text for keyword in jd_list_keywords):
                    user_jds = []
                    if user_id in stored_jd:
                        user_jds = [jd_name for jd_name in stored_jd[user_id].keys() 
                                   if not jd_name.startswith("_")]
                    
                    if user_jds:
                        jd_list_message = f"📋 **등록된 JD 목록** ({len(user_jds)}개):\n\n"
                        for i, jd_name in enumerate(user_jds, 1):
                            jd_list_message += f"{i}. {jd_name}\n"
                        jd_list_message += "\n💡 이력서를 업로드하면 등록된 JD와 매칭 분석을 실시합니다!"
                        send_dm(user_id, jd_list_message)
                    else:
                        send_dm(user_id, "📋 등록된 JD가 없습니다.\n\n\"JD 등록하기\"라고 말씀해주시면 새로운 JD를 등록할 수 있습니다.")
                    return make_response("", 200)
                
                # JD 등록 프로세스 처리
                if user_id in stored_jd and stored_jd[user_id].get("_registration_mode"):
                    mode = stored_jd[user_id]["_registration_mode"]
                    
                    if mode == "waiting_for_jd_name":
                        # JD 이름 저장하고 다음 단계로
                        jd_name = text.strip()
                        if len(jd_name) > 50:
                            send_dm(user_id, "❌ JD 이름이 너무 깁니다. 50자 이내로 입력해주세요.")
                            return make_response("", 200)
                        
                        stored_jd[user_id]["_pending_jd_name"] = jd_name
                        stored_jd[user_id]["_registration_mode"] = "waiting_for_jd_content"
                        
                        send_dm(user_id, f"✅ JD 이름: **{jd_name}**\n\n이제 채용공고 전문을 복사해서 보내주세요.")
                        return make_response("", 200)
                    
                    elif mode == "waiting_for_jd_content":
                        # JD 내용 분석하고 저장
                        jd_content = text.strip()
                        if len(jd_content) < 100:
                            send_dm(user_id, "❌ 채용공고 내용이 너무 짧습니다. 더 자세한 내용을 보내주세요. (최소 100자)")
                            return make_response("", 200)
                        
                        send_dm(user_id, "📋 채용공고를 분석 중입니다...")
                        
                        # JD 분석
                        jd_data = analyze_jd(jd_content)
                        if jd_data:
                            jd_name = stored_jd[user_id]["_pending_jd_name"]
                            
                            # JD 저장
                            if user_id not in stored_jd:
                                stored_jd[user_id] = {}
                            stored_jd[user_id][jd_name] = jd_data
                            
                            # 파일로 저장
                            save_jd_data()
                            
                            # 등록 모드 정리
                            if "_registration_mode" in stored_jd[user_id]:
                                del stored_jd[user_id]["_registration_mode"]
                            if "_pending_jd_name" in stored_jd[user_id]:
                                del stored_jd[user_id]["_pending_jd_name"]
                            
                            # JD 분석 결과 전송
                            blocks = create_jd_analysis_blocks(jd_data, jd_name)
                            send_dm(user_id, f"✅ **{jd_name}** JD가 성공적으로 등록되었습니다!", blocks=blocks)
                        else:
                            send_dm(user_id, "❌ 채용공고 분석에 실패했습니다. 다시 시도해주세요.")
                        
                        return make_response("", 200)
                
                # 일반 검색 및 도움말
                results = search_notion_db(text)
                
                if not results:
                    # 등록된 JD 목록 표시
                    user_jds = []
                    if user_id in stored_jd:
                        user_jds = [jd_name for jd_name in stored_jd[user_id].keys() 
                                   if not jd_name.startswith("_")]
                    
                    jd_list_text = ""
                    if user_jds:
                        jd_list_text = f"\n\n📋 **등록된 JD 목록**: {', '.join(user_jds)}"
                    
                    help_message = f"""
🤖 **사용 방법**:

1. **JD 등록**: "JD 등록하기" 라고 말해주세요
2. **이력서 분석**: 이력서 보관 채널에 파일을 업로드하세요
3. **검색**: 키워드로 기존 분석 결과를 검색할 수 있습니다{jd_list_text}

💡 JD를 먼저 등록하면 이력서 분석 시 매칭 점수도 함께 제공됩니다!
                    """
                    send_dm(user_id, help_message.strip())
                    return make_response("", 200)
                    
                # 검색 결과 전송
                for page in results[:3]:  # 최대 3개 결과만 표시
                    blocks = create_search_result_blocks(page)
                    if blocks:
                        send_dm(user_id, f"🔍 '{text}' 검색 결과입니다.", blocks=blocks)
                    else:
                        send_dm(user_id, "❌ 검색 결과 처리 중 오류가 발생했습니다.")
                        
            except Exception as e:
                logging.error(f"Error processing message event: {str(e)}")
                return make_response("", 200)
                
        # DM에서 "대시보드" 또는 "dashboard" 키워드 감지
        if event.get("channel_type") == "im" and ("대시보드" in text or "dashboard" in text.lower()):
            send_dm(user_id, "💡 대시보드 기능을 사용하려면 `/dashboard` 슬래시 명령어를 사용해주세요!")
            return "OK"
                
    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return make_response("", 200)
    
    return make_response("", 200)

def handle_interactive_message(data):
    # Global variables for PDF generation
    global last_analysis_result, last_analysis_user_id
    
    logging.debug("Handling interactive message: %s", data)
    try:
        # Extract user ID and file URL
        user_id = data["user"]["id"]
        actions = data.get("actions", [])
        if not actions:
            logging.error("No actions in interactive message")
            return make_response("", 200)
        
        action = actions[0]
        action_name = action.get("name")
        
        if action_name == "analyze_resume":
            # JD 없이 이력서만 분석
            file_url = action.get("value")
            if not file_url:
                logging.error("No file URL in action")
                return make_response("", 200)
            
            logging.debug("Processing resume for user %s with file %s", user_id, file_url)
            perform_complete_analysis(user_id, file_url)
                
        elif action_name == "analyze_resume_with_jd":
            # JD 1개가 있는 경우 (자동 선택)
            value = action.get("value")
            if not value or "|" not in value:
                logging.error("Invalid value format for JD analysis")
                return make_response("", 200)
            
            file_url, jd_name = value.split("|", 1)
            logging.debug("Processing resume with JD for user %s, file %s, JD %s", user_id, file_url, jd_name)
            perform_complete_analysis(user_id, file_url, jd_name)
            
        elif action_name == "select_jd_for_analysis":
            # 드롭다운에서 JD 선택한 경우
            selected_option = action.get("selected_options", [{}])[0]
            value = selected_option.get("value")
            if not value or "|" not in value:
                logging.error("Invalid selection value")
                return make_response("", 200)
            
            file_url, jd_name = value.split("|", 1)
            logging.debug("Processing resume with selected JD for user %s, file %s, JD %s", user_id, file_url, jd_name)
            perform_complete_analysis(user_id, file_url, jd_name)
            
        else:
            # 기본적으로 기존 로직 수행 (다운로드 버튼 등)
            logging.debug("Handling other action: %s", action_name)
        
        return make_response("", 200)
        
    except Exception as e:
        logging.error("Error in handle_interactive_message: %s", str(e), exc_info=True)
        try:
            user_id = data.get("user", {}).get("id")
            if user_id:
                send_dm(user_id, "❌ 이력서 처리 중 오류가 발생했습니다.")
        except:
            pass
        return make_response("", 200)

@app.route("/slack/interact", methods=["POST", "GET"])
def slack_interact():
    # Global variables for PDF generation
    global last_analysis_result, last_analysis_user_id
    
    if request.method == "GET":
        return "OK"
    
    try:
        # Content-Type 확인 및 데이터 파싱
        content_type = request.headers.get('Content-Type', '')
        
        if content_type.startswith('application/json'):
            data = request.get_json()
        elif content_type.startswith('application/x-www-form-urlencoded'):
            form_data = request.form
            if 'payload' in form_data:
                data = json.loads(form_data['payload'])
            else:
                # 슬래시 명령어는 /slack/commands로 라우팅되어야 함
                return jsonify({"error": "Use /slack/commands for slash commands"}), 400
        else:
            return jsonify({"error": "Unsupported Content-Type"}), 400
        
        if not data:
            return jsonify({"error": "Empty request"}), 400
        
        # Modal submission 처리
        if data.get("type") == "view_submission":
            return handle_modal_submission(data)
        
        # 버튼 클릭 처리 (기존 코드 복원)
        if data.get("type") == "interactive_message":
            return handle_interactive_message(data)
        
        # Block actions 처리 (PDF 생성 등)
        if data.get("type") == "block_actions":
            try:
                user_id = data.get("user", {}).get("id")
                if not user_id:
                    raise ValueError("User ID not found in payload")
                
                actions = data.get("actions", [])
                if actions:
                    action = actions[0]
                    action_id = action.get("action_id")
                    
                    if action_id == "generate_pdf_report":
                        # PDF 보고서 생성
                        try:
                            if last_analysis_result is None:
                                send_dm(user_id, "❌ 분석 결과가 없습니다. 먼저 이력서를 분석해주세요.")
                                return make_response("", 200)
                            
                            # 기술 스킬 차트 재생성
                            tech_skills = last_analysis_result.get("skill_cards", {}).get("tech_skills", "")
                            skills_dict = parse_skills(tech_skills)
                            chart_image = None
                            
                            if skills_dict:
                                chart_image = create_plotly_radar_chart(skills_dict)
                            
                            # PDF 생성
                            pdf_bytes = create_pdf_report(last_analysis_result, chart_image)
                            
                            if pdf_bytes:
                                # DM 채널 ID 가져오기
                                dm_response = client.conversations_open(users=[user_id])
                                if dm_response.get("ok"):
                                    dm_channel_id = dm_response["channel"]["id"]
                                    
                                    # PDF 업로드
                                    temp_pdf_file = f"resume_analysis_report_{user_id}.pdf"
                                    with open(temp_pdf_file, 'wb') as f:
                                        f.write(pdf_bytes)
                                    
                                    upload_response = client.files_upload_v2(
                                        channel=dm_channel_id,
                                        title="이력서 분석 보고서",
                                        filename="resume_analysis_report.pdf",
                                        file=temp_pdf_file,
                                        initial_comment="📊 이력서 분석 보고서가 생성되었습니다!"
                                    )
                                    
                                    # 임시 파일 삭제
                                    if os.path.exists(temp_pdf_file):
                                        os.remove(temp_pdf_file)
                                    
                                    if upload_response.get("file"):
                                        logging.info("PDF report uploaded successfully")
                                else:
                                    send_dm(user_id, "❌ DM 채널 정보를 가져올 수 없습니다.")
                            else:
                                send_dm(user_id, "❌ PDF 생성에 실패했습니다.")
                        
                        except Exception as e:
                            logging.error(f"PDF generation error: {str(e)}")
                            send_dm(user_id, f"❌ PDF 생성 중 오류가 발생했습니다: {str(e)}")
                        
                        return make_response("", 200)
                    
                    elif action_id == "refresh_market_data":
                        # 시장 데이터 새로고침
                        try:
                            # 사용자에게 로딩 메시지 전송
                            send_dm(user_id, "🔄 실시간 채용 데이터를 수집하고 있습니다... (약 10초 소요)")
                            
                            # 업데이트된 Modal 생성 (강제 스크래핑 실행)
                            updated_modal = create_market_intelligence_modal(force_scraping=True)
                            
                            # Modal 업데이트
                            response = client.views_update(
                                view_id=data["view"]["id"],
                                hash=data["view"]["hash"],
                                view=updated_modal
                            )
                            
                            if response.get("ok"):
                                logging.info("Market intelligence modal refreshed successfully")
                                send_dm(user_id, "✅ 최신 채용 시장 데이터로 업데이트 완료!")
                            else:
                                logging.error(f"Failed to refresh modal: {response}")
                                send_dm(user_id, "❌ 데이터 새로고침에 실패했습니다.")
                            
                        except Exception as e:
                            logging.error(f"Market data refresh error: {str(e)}")
                            send_dm(user_id, f"❌ 데이터 새로고침 중 오류가 발생했습니다: {str(e)}")
                        
                        return make_response("", 200)
                
                return make_response("", 200)
                
            except Exception as e:
                logging.error(f"Error handling block actions: {str(e)}")
                return make_response("", 200)
            
        return make_response("", 200)
        
    except Exception as e:
        logging.error(f"Error in slack_interact: {str(e)}")
        return make_response("", 500)

# 슬래시 명령어 처리 함수 추가
def handle_slash_command(form_data):
    """슬래시 명령어 처리"""
    try:
        command = form_data.get('command', '')
        user_id = form_data.get('user_id', '')
        trigger_id = form_data.get('trigger_id', '')
        
        logging.info(f"Received slash command: {command} from user: {user_id}")
        
        if command == '/dashboard':
            logging.info("=== 이력서 대시보드 명령어 처리 시작 ===")
            
            if not trigger_id:
                return jsonify({"text": "❌ trigger_id가 없습니다."}), 200
            
            try:
                # 기존 이력서 관리 대시보드 모달 열기
                dashboard_modal = create_dashboard_modal()
                
                response = client.views_open(
                    trigger_id=trigger_id,
                    view=dashboard_modal
                )
                
                if not response.get("ok"):
                    logging.error(f"대시보드 모달 열기 실패: {response}")
                    return jsonify({"text": f"❌ 모달 열기 실패: {response.get('error', '알 수 없음')}"}), 200
                
                logging.info("이력서 대시보드 모달 열기 성공")
                return "", 200
                
            except Exception as e:
                logging.error(f"이력서 대시보드 모달 처리 오류: {str(e)}", exc_info=True)
                return jsonify({"text": f"❌ 대시보드 생성 중 오류가 발생했습니다: {str(e)}"}), 200
        
        elif command == '/market':
            logging.info("=== 시장 인텔리전스 명령어 처리 시작 ===")
            
            if not trigger_id:
                return jsonify({"text": "❌ trigger_id가 없습니다."}), 200
            
            try:
                # 즉시 로딩 모달 표시
                loading_modal = {
                    "type": "modal",
                    "callback_id": "market_loading",
                    "title": {
                        "type": "plain_text",
                        "text": "📊 채용 시장 인텔리전스"
                    },
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "🔄 *실시간 채용 시장 데이터 수집 중...*\n\n• 원티드 채용공고 스크래핑 중\n• 기업별 통계 분석 중\n• 기술스택 트렌드 분석 중\n\n잠시만 기다려주세요! ⏳"
                            }
                        }
                    ],
                    "close": {
                        "type": "plain_text",
                        "text": "취소"
                    }
                }
                
                # 로딩 모달 열기
                response = client.views_open(
                    trigger_id=trigger_id,
                    view=loading_modal
                )
                
                if not response.get("ok"):
                    logging.error(f"로딩 모달 열기 실패: {response}")
                    return jsonify({"text": f"❌ 모달 열기 실패: {response.get('error', '알 수 없음')}"}), 200
                
                view_id = response["view"]["id"]
                logging.info(f"로딩 모달 열기 성공, view_id: {view_id}")
                
                # 백그라운드에서 실제 데이터 처리
                def process_market_modal():
                    try:
                        logging.info("백그라운드 시장 인텔리전스 데이터 처리 시작")
                        
                        # 실제 스크래핑 시도
                        try:
                            scraped_jobs = scrape_wanted_jobs()
                            if scraped_jobs and len(scraped_jobs) > 0:
                                logging.info(f"실제 스크래핑 성공: {len(scraped_jobs)}개 공고")
                                analyzed_data = analyze_scraped_data(scraped_jobs)
                                data_source = "실시간 데이터"
                            else:
                                logging.warning("스크래핑 결과 없음, 목업 데이터 사용")
                                analyzed_data = get_mock_data()
                                data_source = "데모 데이터"
                        except Exception as e:
                            logging.error(f"스크래핑 실패: {str(e)}")
                            analyzed_data = get_mock_data()
                            data_source = "데모 데이터"
                        
                        # 시장 인텔리전스 모달 생성
                        market_modal = create_market_intelligence_modal_with_data(analyzed_data, data_source)
                        
                        # 모달 업데이트
                        update_response = client.views_update(
                            view_id=view_id,
                            view=market_modal
                        )
                        
                        if update_response.get("ok"):
                            logging.info("시장 인텔리전스 모달 업데이트 성공")
                        else:
                            logging.error(f"모달 업데이트 실패: {update_response}")
                            
                    except Exception as e:
                        logging.error(f"백그라운드 시장 인텔리전스 처리 오류: {str(e)}", exc_info=True)
                        
                        # 에러 모달 표시
                        error_modal = {
                            "type": "modal",
                            "callback_id": "market_error",
                            "title": {
                                "type": "plain_text",
                                "text": "❌ 오류 발생"
                            },
                            "blocks": [
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*시장 인텔리전스 생성 중 오류가 발생했습니다.*\n\n```{str(e)}```\n\n잠시 후 다시 시도해주세요."
                                    }
                                }
                            ],
                            "close": {
                                "type": "plain_text",
                                "text": "닫기"
                            }
                        }
                        
                        try:
                            client.views_update(view_id=view_id, view=error_modal)
                        except:
                            pass
                
                # 백그라운드 스레드 시작
                import threading
                thread = threading.Thread(target=process_market_modal)
                thread.daemon = True
                thread.start()
                
                return "", 200
                
            except Exception as e:
                logging.error(f"시장 인텔리전스 모달 처리 오류: {str(e)}", exc_info=True)
                return jsonify({"text": f"❌ 시장 인텔리전스 생성 중 오류가 발생했습니다: {str(e)}"}), 200
        
        return "알 수 없는 명령어입니다.", 200
        
    except Exception as e:
        logging.error(f"슬래시 커맨드 전체 오류: {str(e)}", exc_info=True)
        return f"명령어 처리 중 오류가 발생했습니다: {str(e)}", 200

# Modal submission 처리 함수 추가
def handle_modal_submission(data):
    """Modal 제출 처리"""
    try:
        callback_id = data.get("view", {}).get("callback_id", "")
        user_id = data.get("user", {}).get("id", "")
        
        if callback_id == "dashboard_modal":
            # 필터 값들 추출
            state_values = data.get("view", {}).get("state", {}).get("values", {})
            
            job_filter = "all"
            years_filter = "all" 
            sort_filter = "latest"
            
            if "job_filter" in state_values:
                job_selection = state_values["job_filter"]["select_job"].get("selected_option")
                if job_selection:
                    job_filter = job_selection["value"]
            
            if "years_filter" in state_values:
                years_selection = state_values["years_filter"]["select_years"].get("selected_option")
                if years_selection:
                    years_filter = years_selection["value"]
                    
            if "sort_filter" in state_values:
                sort_selection = state_values["sort_filter"]["select_sort"].get("selected_option")
                if sort_selection:
                    sort_filter = sort_selection["value"]
            
            logging.info(f"Dashboard filters: job={job_filter}, years={years_filter}, sort={sort_filter}")
            
            # Notion 데이터 조회 및 필터링
            try:
                notion_pages = get_all_resumes_from_notion()
                all_data = parse_notion_resume_data(notion_pages)
                filtered_data = apply_filters(all_data, job_filter, years_filter, sort_filter)
                
                logging.info(f"Found {len(all_data)} total resumes, {len(filtered_data)} after filtering")
                
                # 차트 생성
                chart_image = create_dashboard_chart(filtered_data)
                
                # 새로운 Modal 컨텐츠 생성
                new_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*📊 이력서 대시보드 - 필터 결과*"
                        }
                    },
                    {
                        "type": "divider"
                    }
                ]
                
                # 필터링된 결과 블록 추가
                result_blocks = create_filtered_results_blocks(filtered_data, len(all_data))
                new_blocks.extend(result_blocks)
                
                # 차트가 있는 경우 업로드
                if chart_image:
                    try:
                        chart_file_id = upload_image_to_slack(chart_image, "매칭률 분포 차트", user_id)
                        if chart_file_id:
                            new_blocks.insert(-1, {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "📊 *매칭률 분포 차트가 DM으로 전송되었습니다.*"
                                }
                            })
                    except Exception as chart_error:
                        logging.error(f"Chart upload error: {str(chart_error)}")
                
                # Modal 업데이트
                updated_modal = {
                    "type": "modal",
                    "callback_id": "dashboard_modal",
                    "title": {
                        "type": "plain_text",
                        "text": "📊 이력서 대시보드"
                    },
                    "blocks": new_blocks,
                    "close": {
                        "type": "plain_text",
                        "text": "닫기"
                    }
                }
                
                # Modal 업데이트 시도
                view_id = data.get("view", {}).get("id")
                if view_id:
                    update_response = client.views_update(
                        view_id=view_id,
                        view=updated_modal
                    )
                    
                    if update_response.get("ok"):
                        logging.info("Modal updated successfully")
                    else:
                        logging.error(f"Failed to update modal: {update_response}")
                
                return "", 200
                
            except Exception as data_error:
                logging.error(f"Data processing error: {str(data_error)}")
                
                # 에러 Modal 업데이트
                error_modal = {
                    "type": "modal",
                    "callback_id": "dashboard_modal",
                    "title": {
                        "type": "plain_text",
                        "text": "📊 이력서 대시보드"
                    },
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "❌ *데이터를 불러오는 중 오류가 발생했습니다.*\n잠시 후 다시 시도해주세요."
                            }
                        }
                    ],
                    "close": {
                        "type": "plain_text",
                        "text": "닫기"
                    }
                }
                
                view_id = data.get("view", {}).get("id")
                if view_id:
                    client.views_update(view_id=view_id, view=error_modal)
                
                return "", 200
        
        return "", 200
        
    except Exception as e:
        logging.error(f"Error handling modal submission: {str(e)}")
        return "", 500

def replace_emojis_for_pdf(text):
    """PDF용으로 이모지를 텍스트로 변환"""
    emoji_replacements = {
        '📊': '[차트]',
        '👤': '[인물]',
        '💫': '[별]',
        '⭐': '[별점]',
        '📈': '[그래프]',
        '🎯': '[목표]',
        '📌': '[핀]',
        '💻': '[컴퓨터]',
        '👥': '[사람들]',
        '✅': '[체크]',
        '📚': '[책]',
        '🔍': '[검색]',
        '❌': '[X]',
        '📋': '[클립보드]',
        '🎉': '[축하]',
        '💡': '[전구]',
        '🚀': '[로켓]',
        '🏆': '[트로피]',
        '📝': '[메모]',
        '⚡': '[번개]',
        '🌟': '[별]',
        '🎨': '[팔레트]',
        '🔧': '[도구]',
        '📖': '[열린책]',
        '🎭': '[연극]',
        '🎪': '[서커스]',
        '🎸': '[기타]',
        '🎯': '[다트]'
    }
    
    result = text
    for emoji, replacement in emoji_replacements.items():
        result = result.replace(emoji, replacement)
    
    return result

def create_pdf_report(result, chart_image=None):
    """분석 결과를 PDF 보고서로 생성 (reportlab 사용)"""
    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1*inch, bottomMargin=1*inch)
        
        # 한글 폰트 등록
        try:
            # 맑은 고딕 폰트 등록
            pdfmetrics.registerFont(TTFont('MalgunGothic', 'C:/Windows/Fonts/malgun.ttf'))
            pdfmetrics.registerFont(TTFont('MalgunGothic-Bold', 'C:/Windows/Fonts/malgunbd.ttf'))
            font_name = 'MalgunGothic'
            bold_font_name = 'MalgunGothic-Bold'
        except:
            try:
                # 대체 폰트: 나눔고딕
                pdfmetrics.registerFont(TTFont('NanumGothic', 'C:/Windows/Fonts/NanumGothic.ttf'))
                pdfmetrics.registerFont(TTFont('NanumGothic-Bold', 'C:/Windows/Fonts/NanumGothicBold.ttf'))
                font_name = 'NanumGothic'
                bold_font_name = 'NanumGothic-Bold'
            except:
                # 기본 폰트 사용 (한글 깨질 수 있음)
                font_name = 'Helvetica'
                bold_font_name = 'Helvetica-Bold'
        
        # 스타일 설정 (한글 폰트 적용)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Title'],
            fontName=bold_font_name,
            fontSize=24,
            spaceAfter=30,
            textColor=colors.darkblue,
            alignment=1  # center
        )
        
        heading_style = ParagraphStyle(
            'HeadingStyle',
            parent=styles['Heading2'],
            fontName=bold_font_name,
            fontSize=14,
            spaceAfter=12,
            textColor=colors.darkblue,
            spaceBefore=20
        )
        
        normal_style = ParagraphStyle(
            'NormalStyle',
            parent=styles['Normal'],
            fontName=font_name,
            fontSize=11,
            spaceAfter=8,
            leftIndent=20
        )
        
        info_style = ParagraphStyle(
            'InfoStyle',
            parent=styles['Normal'],
            fontName=font_name,
            fontSize=10,
            spaceAfter=8
        )
        
        # PDF 컨텐츠 구성
        story = []
        
        # 제목
        story.append(Paragraph(replace_emojis_for_pdf("📊 이력서 분석 보고서"), title_style))
        story.append(Spacer(1, 20))
        
        # 기본 정보
        current_time = datetime.now().strftime('%Y년 %m월 %d일 %H:%M')
        story.append(Paragraph(f"생성일시: {current_time}", info_style))
        story.append(Spacer(1, 20))
        
        # 분석 대상자 정보
        story.append(Paragraph(replace_emojis_for_pdf("👤 분석 대상자 정보"), heading_style))
        story.append(Paragraph(f"• 이름: {result.get('name', '미기재')}", normal_style))
        story.append(Paragraph(f"• 총 경력: {result.get('total_years', 'N/A')}년", normal_style))
        story.append(Spacer(1, 15))
        
        # 캐치프레이즈
        story.append(Paragraph(replace_emojis_for_pdf("💫 캐치프레이즈"), heading_style))
        catchphrase = result.get('catchphrase', 'N/A')
        story.append(Paragraph(f"'{replace_emojis_for_pdf(catchphrase)}'", normal_style))
        story.append(Spacer(1, 15))
        
        # 강점 Top 3
        story.append(Paragraph(replace_emojis_for_pdf("⭐ 강점 Top 3"), heading_style))
        for i, strength in enumerate(result.get('top_strengths', []), 1):
            story.append(Paragraph(f"{i}. {replace_emojis_for_pdf(strength)}", normal_style))
        story.append(Spacer(1, 15))
        
        # 기술 스킬 차트 (있는 경우)
        if chart_image:
            story.append(Paragraph(replace_emojis_for_pdf("📈 기술 스킬 레이더 차트"), heading_style))
            try:
                # 차트 이미지를 메모리에서 직접 처리 (임시 파일 없이)
                from PIL import Image
                import io
                
                # BytesIO 객체에서 이미지 생성
                image_buffer = io.BytesIO(chart_image)
                pil_image = Image.open(image_buffer)
                
                # PIL 이미지를 reportlab Image로 직접 변환
                chart_img = RLImage(pil_image, width=5*inch, height=5*inch)
                story.append(chart_img)
                story.append(Spacer(1, 15))
                
                logging.info("Chart image successfully added to PDF")
                    
            except Exception as e:
                logging.error(f"Chart image processing error: {str(e)}")
                story.append(Paragraph(replace_emojis_for_pdf("📈 기술 스킬 레이더 차트"), heading_style))
                story.append(Paragraph("차트 이미지를 포함할 수 없습니다.", normal_style))
                story.append(Spacer(1, 15))
        
        # 역량 카드
        story.append(Paragraph(replace_emojis_for_pdf("🎯 역량 분석"), heading_style))
        
        skill_cards = result.get('skill_cards', {})
        
        # Domain Knowledge
        story.append(Paragraph(replace_emojis_for_pdf("📌 Domain Knowledge"), heading_style))
        domain_knowledge = skill_cards.get('domain_knowledge', 'N/A')
        story.append(Paragraph(replace_emojis_for_pdf(domain_knowledge), normal_style))
        story.append(Spacer(1, 10))
        
        # Tech Skills
        story.append(Paragraph(replace_emojis_for_pdf("💻 Tech Skills"), heading_style))
        tech_skills = skill_cards.get('tech_skills', 'N/A')
        story.append(Paragraph(replace_emojis_for_pdf(tech_skills), normal_style))
        story.append(Spacer(1, 10))
        
        # Soft Skills
        story.append(Paragraph(replace_emojis_for_pdf("👥 Soft Skills"), heading_style))
        soft_skills = skill_cards.get('soft_skills', 'N/A')
        story.append(Paragraph(replace_emojis_for_pdf(soft_skills), normal_style))
        
        # PDF 생성
        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()
        
    except Exception as e:
        logging.error(f"PDF generation error: {str(e)}")
        raise

def create_stat_card_blocks(result):
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 {result.get('name', '')}님의 이력서 분석 결과",
                "emoji": True
            }
        },
        {
            "type": "divider"
        },
        # 기본 정보
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*📈 총 경력 연차*\n{result.get('total_years', 'N/A')}년"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*📅 분석 완료*\n{datetime.now().strftime('%Y.%m.%d')}"
                }
            ]
        },
        {
            "type": "divider"
        },
        # 캐치프레이즈
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*💫 캐치프레이즈*\n_{result.get('catchphrase', 'N/A')}_"
            }
        },
        {
            "type": "divider"
        },
        # 강점 Top 3
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*⭐ 강점 Top 3*"
            }
        }
    ]
    
    # 강점들을 표시
    strengths = result.get('top_strengths', [])
    if strengths:
        if len(strengths) >= 2:
            blocks.append({
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*1️⃣ {strengths[0]}*"
                    },
                    {
                        "type": "mrkdwn", 
                        "text": f"*2️⃣ {strengths[1]}*"
                    }
                ]
            })
        
        if len(strengths) >= 3:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*3️⃣ {strengths[2]}*"
                }
            })
    
    blocks.extend([
        {
            "type": "divider"
        },
        # Domain Knowledge
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🎯 Domain Knowledge*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{result.get('skill_cards', {}).get('domain_knowledge', 'N/A')}"
            }
        },
        {
            "type": "divider"
        },
        # Tech Skills
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*💻 Tech Skills*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{result.get('skill_cards', {}).get('tech_skills', 'N/A')}"
            }
        },
        {
            "type": "divider"
        },
        # Soft Skills
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*👥 Soft Skills*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{result.get('skill_cards', {}).get('soft_skills', 'N/A')}"
            }
        },
        {
            "type": "divider"
        },
        # 추가 액션
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📊 추가 액션*"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📄 PDF 보고서 다운로드",
                        "emoji": True
                    },
                    "action_id": "generate_pdf_report",
                    "style": "primary"
                }
            ]
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ 분석 완료! 결과는 Notion에도 자동 저장되었습니다."
                }
            ]
        }
    ])
    
    return blocks

def safe_text(t):
    """텍스트를 안전하게 처리하는 함수"""
    if isinstance(t, bytearray):
        return t.decode("utf-8", errors="ignore")
    elif isinstance(t, bytes):
        return t.decode("utf-8", errors="ignore")
    return str(t)

def upload_to_slack(file_content, filename, user_id):
    """생성된 PDF를 Slack에 업로드"""
    try:
        files = {
            'file': (filename, file_content, 'application/pdf')
        }
        params = {
            'channels': user_id,
            'initial_comment': '📊 이력서 분석 보고서가 생성되었습니다.'
        }
        response = requests.post(
            'https://slack.com/api/files.upload',
            headers={'Authorization': f'Bearer {SLACK_BOT_TOKEN}'},
            params=params,
            files=files
        )
        return response.json()
    except Exception as e:
        print(f"Slack upload error: {str(e)}")
        raise

def create_simple_test_chart():
    """간단한 테스트 차트 생성"""
    logging.debug("Creating simple test chart")
    
    try:
        # 간단한 SVG 차트
        svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="400" height="400" xmlns="http://www.w3.org/2000/svg">
    <style>
        .title { font-family: Arial; font-size: 16px; fill: #333; }
        .bar { fill: #4CAF50; }
        .label { font-family: Arial; font-size: 12px; fill: #333; }
    </style>
    
    <text x="200" y="30" text-anchor="middle" class="title">기술 스킬 차트</text>
    
    <rect x="50" y="60" width="120" height="30" class="bar"/>
    <text x="60" y="80" class="label">Python: 90%</text>
    
    <rect x="50" y="100" width="100" height="30" class="bar"/>
    <text x="60" y="120" class="label">SQL: 80%</text>
    
    <rect x="50" y="140" width="110" height="30" class="bar"/>
    <text x="60" y="160" class="label">Figma: 85%</text>
    
    <rect x="50" y="180" width="90" height="30" class="bar"/>
    <text x="60" y="200" class="label">GCP: 75%</text>
</svg>'''
        
        svg_bytes = svg.encode('utf-8')
        logging.debug("Simple test chart created, size: %d bytes", len(svg_bytes))
        return svg_bytes
        
    except Exception as e:
        logging.error("Error creating simple test chart: %s", str(e))
        return None

def create_plotly_radar_chart(skills_dict):
    """matplotlib을 사용한 레이더 차트 생성 (PNG 형식)"""
    logging.debug("Creating radar chart with matplotlib - skills: %s", skills_dict)
    
    if not skills_dict:
        logging.error("Empty skills dictionary provided")
        return None
        
    try:
        # 데이터 정제 - 상위 8개 스킬만 선택
        sorted_skills = dict(sorted(skills_dict.items(), key=lambda x: x[1], reverse=True)[:8])
        logging.debug("Selected top 8 skills: %s", sorted_skills)
        
        # 데이터 준비
        categories = list(sorted_skills.keys())
        values = list(sorted_skills.values())
        N = len(categories)
        
        if N == 0:
            logging.error("No skills to chart")
            return None
        
        # 각도 계산 (360도를 N개로 분할)
        angles = [n / N * 2 * np.pi for n in range(N)]
        angles += angles[:1]  # 원을 완성하기 위해 첫 번째 값을 마지막에 추가
        
        # 값도 순환 완성
        values += values[:1]
        
        # 그래프 생성
        fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(projection='polar'))
        
        # 차트 색상 설정
        ax.plot(angles, values, 'o-', linewidth=2, label='기술 스킬', color='#36A2EB')
        ax.fill(angles, values, alpha=0.25, color='#36A2EB')
        
        # 카테고리 라벨 설정
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=11)
        
        # Y축 (반지름) 설정
        ax.set_ylim(0, 100)
        ax.set_yticks([20, 40, 60, 80, 100])
        ax.set_yticklabels(['20%', '40%', '60%', '80%', '100%'], fontsize=9)
        
        # 격자 스타일 설정
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#FAFAFA')
        
        # 제목 설정
        plt.title('기술 스킬 레이더 차트', size=16, fontweight='bold', pad=20)
        
        # 범례 설정
        plt.legend(loc='upper right', bbox_to_anchor=(1.2, 1.0))
        
        # 여백 조정
        plt.tight_layout()
        
        # PNG 바이트로 변환
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight', 
                   facecolor='white', edgecolor='none')
        buffer.seek(0)
        png_bytes = buffer.read()
        buffer.close()
        
        # 메모리 정리
        plt.close(fig)
        
        logging.debug("Generated PNG size: %d bytes", len(png_bytes))
        return png_bytes
        
    except Exception as e:
        logging.error("Error creating radar chart: %s", str(e), exc_info=True)
        return None

def create_wordcloud(skills_dict):
    """기술 스킬 워드클라우드 생성"""
    if not skills_dict:
        return None
    
    # 데이터 전처리
    word_freq = {}
    for skill, value in skills_dict.items():
        try:
            # Remove '%' and convert to float
            freq = float(str(value).replace('%', '').strip())
            word_freq[skill] = freq
        except (ValueError, TypeError):
            word_freq[skill] = 1
    
    # WordCloud 설정
    wordcloud = WordCloud(
        width=800,
        height=400,
        background_color='white',
        font_path='C:/Windows/Fonts/malgun.ttf',  # 한글 폰트 경로
        min_font_size=10,
        max_font_size=100,
        prefer_horizontal=0.7
    )
    
    # 워드클라우드 생성
    wordcloud.generate_from_frequencies(word_freq)
    
    # 이미지로 저장
    plt.figure(figsize=(10, 5))
    plt.imshow(wordcloud, interpolation='bilinear')
    plt.axis('off')
    
    # 이미지로 저장
    img_bytes = io.BytesIO()
    plt.savefig(img_bytes, format='png', bbox_inches='tight', dpi=300)
    img_bytes.seek(0)
    plt.close()
    
    return img_bytes

def parse_skills(skills_text):
    """스킬 텍스트에서 스킬:퍼센트 형식을 파싱"""
    logging.debug("Starting skills parsing from text: %s", skills_text)
    
    if not skills_text:
        logging.error("Empty skills text provided")
        return {}
        
    try:
        # 더 유연한 정규표현식 패턴
        # Matches: "Skill Name: 90%" or "Skill Name:90%" or "Skill Name: 90" or "Skill/Name: 90%"
        skill_pattern = r'([^,]+?):\s*(\d+)%?'
        matches = re.findall(skill_pattern, skills_text)
        
        if not matches:
            logging.error("No skills found in text using pattern: %s", skill_pattern)
            return {}
            
        # Convert to dictionary and clean skill names
        skills_dict = {}
        for skill, percentage in matches:
            skill_name = skill.strip()
            try:
                percentage_value = int(percentage.strip())
                if 0 <= percentage_value <= 100:  # Validate percentage range
                    skills_dict[skill_name] = percentage_value
                    logging.debug("Parsed skill: %s = %d%%", skill_name, percentage_value)
                else:
                    logging.warning("Invalid percentage value for skill %s: %d", skill_name, percentage_value)
            except ValueError as ve:
                logging.error("Error converting percentage for skill %s: %s", skill_name, str(ve))
                
        if not skills_dict:
            logging.error("Failed to parse any valid skills")
        else:
            logging.info("Successfully parsed %d skills: %s", len(skills_dict), skills_dict)
            
        return skills_dict
        
    except Exception as e:
        logging.error("Error parsing skills: %s", str(e), exc_info=True)
        return {}

def upload_image_to_slack(image_bytes, title, channel_id):
    """PNG 이미지를 Slack에 업로드"""
    logging.debug("Starting Slack image upload - Title: %s, Channel: %s", title, channel_id)
    
    if not image_bytes:
        logging.error("No image bytes provided for upload")
        return None
        
    if not channel_id:
        logging.error("No channel ID provided")
        return None
        
    logging.debug("Image size: %d bytes", len(image_bytes))
    
    # Determine file extension based on image content
    file_extension = "png" if image_bytes.startswith(b'\x89PNG') else "svg"
    temp_file = f"temp_{title}.{file_extension}"
    
    try:
        with open(temp_file, 'wb') as f:
            f.write(image_bytes)
        logging.debug("Temporary file created: %s", temp_file)
        
        # Upload file to Slack
        logging.debug("Attempting to upload file to Slack")
        try:
            response = client.files_upload_v2(
                channel=channel_id,
                title=title,
                filename=f"{title}.{file_extension}",
                file=temp_file,
                request_file_info=True
            )
            logging.debug("Slack upload response: %s", response)
            
            if response and response.get("file"):
                file_id = response["file"]["id"]
                file_url = response["file"].get("url_private")
                logging.info("File uploaded successfully. File ID: %s, URL: %s", file_id, file_url)
                
                # For PNG files, try to display as inline image
                if file_extension == "png":
                    try:
                        blocks = [
                            {
                                "type": "image",
                                "title": {
                                    "type": "plain_text",
                                    "text": title
                                },
                                "image_url": file_url,
                                "alt_text": title
                            }
                        ]
                        
                        client.chat_postMessage(
                            channel=channel_id,
                            blocks=blocks,
                            text=f"📊 {title}"
                        )
                        logging.debug("Posted PNG image block to channel")
                    except Exception as block_error:
                        logging.error("Error posting image block: %s", str(block_error))
                
                return file_id
            else:
                logging.error("Upload response missing file info: %s", response)
                return None
                
        except Exception as upload_error:
            logging.error("Error during Slack upload: %s", str(upload_error), exc_info=True)
            return None
            
    except Exception as file_error:
        logging.error("Error handling temporary file: %s", str(file_error))
        return None
        
    finally:
        # Clean up temporary file
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                logging.debug("Cleaned up temporary upload file")
        except Exception as cleanup_error:
            logging.error("Error cleaning up upload file: %s", str(cleanup_error))
            
    return None

def download_resume(file_url):
    """Download resume file from Slack"""
    logging.debug("Attempting to download resume from URL: %s", file_url)
    try:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        res = requests.get(file_url, headers=headers, timeout=30)
        
        if res.status_code != 200:
            logging.error("File download failed: Status code %d", res.status_code)
            return None

        file_path = "temp.pdf"
        with open(file_path, "wb") as f:
            f.write(res.content)
            
        # Extract text from PDF
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
                
        if not text.strip():
            logging.error("No text could be extracted from the PDF")
            return None
            
        logging.debug("Successfully extracted text from PDF, length: %d", len(text))
        return text
        
    except Exception as e:
        logging.error("Error downloading resume: %s", str(e), exc_info=True)
        return None

def analyze_resume(text, user_id=None):
    """Analyze resume text using GPT-4"""
    logging.debug("Starting resume analysis")
    try:
        openai_client = OpenAI(api_key=GPT_API_KEY)
        prompt = f"""
        이력서를 분석하여 정확히 아래 JSON 형식으로만 출력하세요. 반드시 한국어로 응답해주세요.

        이력서 내용:
        {text}

        *** 중요 지침 ***
        - 모든 응답은 반드시 한국어로 작성
        - 영어 단어나 문장 사용 금지
        - 기술명은 원래 영어 그대로 유지 (예: Python, JavaScript)
        - 나머지 모든 설명과 내용은 한국어로 작성

        응답은 반드시 다음 형식의 JSON만 출력:
        {{
            "name": "이름 (없으면 '미기재'로 표시)",
            "total_years": 숫자만_입력,
            "top_strengths": [
                "강점1 (한국어로)",
                "강점2 (한국어로)",
                "강점3 (한국어로)"
            ],
            "catchphrase": "한 문장으로 된 캐치프레이즈 (한국어로)",
            "skill_cards": {{
                "domain_knowledge": "도메인 지식 설명 (한국어로)",
                "tech_skills": "각 기술의 숙련도를 백분율로 표시 (예: Python: 90%, Java: 80%, JavaScript: 75%)",
                "soft_skills": "소프트 스킬 설명 (한국어로)"
            }}
        }}"""

        logging.info("API 요청 시작 - openai_client: %s", openai_client)
        logging.info("API 요청 데이터: %s", text[:100] + "...")
        
        print("GPT API 호출 시작...")
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a Korean resume analyzer. You MUST respond in Korean language ONLY. All descriptions, explanations, and content must be in Korean. Only technical terms (like programming languages, tools) can remain in English. Always output ONLY valid JSON format in Korean."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        print("GPT API 호출 완료")

        result = response.choices[0].message.content.strip()
        print(f"분석 결과: {result[:200]}...")

        # JSON 형식 검증
        try:
            parsed_result = json.loads(result)
            return parsed_result
            
        except Exception as e:
            error_msg = f"분석 결과 처리 중 오류가 발생했습니다: {str(e)}"
            print(error_msg)
            if user_id:
                send_dm(user_id, f"❌ {error_msg}")
            return None

    except Exception as e:
        logging.error("Error analyzing resume: %s", str(e), exc_info=True)
        if user_id:
            send_dm(user_id, f"❌ 분석 중 오류 발생: {str(e)}")
        return None

def analyze_jd(jd_text):
    """JD 텍스트를 분석하여 요구사항 추출"""
    try:
        openai_client = OpenAI(api_key=GPT_API_KEY)
        prompt = f"""
        다음 채용공고(JD)를 분석하여 JSON 형식으로 요구사항을 추출해주세요:

        JD 내용:
        {jd_text}

        다음 형식의 JSON만 출력:
        {{
            "position": "채용 포지션명",
            "required_skills": ["필수 기술1", "필수 기술2", "필수 기술3"],
            "preferred_skills": ["우대 기술1", "우대 기술2", "우대 기술3"],
            "required_experience": 최소경력년수_숫자,
            "preferred_experience": 우대경력년수_숫자,
            "education": "학력 요구사항",
            "responsibilities": ["주요 업무1", "주요 업무2", "주요 업무3"],
            "company_culture": "회사 문화나 인재상",
            "domain": "업무 도메인 (예: 핀테크, 이커머스, AI 등)"
        }}
        """

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a JD analyzer that outputs ONLY valid JSON format."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        result = response.choices[0].message.content.strip()
        return json.loads(result)

    except Exception as e:
        logging.error(f"JD 분석 오류: {str(e)}")
        return None

def calculate_matching_score(resume_result, jd_data, original_resume_text=None):
    """이력서와 JD 매칭 점수 계산 (범용적 의미적 매칭)"""
    try:
        openai_client = OpenAI(api_key=GPT_API_KEY)
        
        # 디버깅: 입력 데이터 로깅
        print("=== 매칭 분석 디버깅 정보 ===")
        print(f"이력서 요약 데이터: {json.dumps(resume_result, ensure_ascii=False, indent=2)}")
        print(f"원본 이력서 텍스트 길이: {len(original_resume_text) if original_resume_text else 0}자")
        if original_resume_text:
            print(f"원본 이력서 샘플: {original_resume_text[:300]}...")
        print(f"JD 데이터: {json.dumps(jd_data, ensure_ascii=False, indent=2)}")
        print("=" * 50)
        
        # 범용적이고 적극적인 의미적 매칭 프롬프트
        prompt = f"""
        이력서와 JD를 의미적으로 매칭 분석하여 JSON 형식으로만 출력하세요. 모든 응답은 한국어로 작성해주세요.

        *** 핵심 매칭 철학 ***
        1. 업무의 본질과 목적이 비슷하면 키워드가 달라도 반드시 매칭으로 판단
        2. 직접적 경험뿐만 아니라 관련 경험, 유사 경험도 적극적으로 매칭으로 인정
        3. 부분적 경험이라도 해당 분야와 연관성이 있으면 긍정적으로 평가
        4. 의심스러우면 매칭으로 판단 (보수적이지 말고 적극적으로 매칭)

        *** 의미적 매칭 가이드라인 ***
        - 동의어와 유사 표현을 적극적으로 인정 (예: 소싱↔발굴, 관리↔운영, 기획↔설계)
        - 상위/하위 개념도 매칭으로 인정 (예: 채용 경험 → 인재 관련 업무 매칭)
        - 업무 맥락이 비슷하면 매칭 (예: 교육/멘토링 → 사람 관리 관련 업무)
        - 도구나 방법론의 차이는 무시하고 업무 목적에 집중
        - 영어/한국어 혼용 표현의 의미적 동일성 인정

        *** 적극적 매칭 원칙 ***
        - 이력서에서 관련 키워드나 맥락을 찾았다면 "매칭 없음"이 아닌 "매칭 있음"으로 판단
        - JD 요구사항의 핵심 업무와 이력서 경험의 핵심이 겹치면 매칭
        - 완벽한 일치를 요구하지 말고, 연관성과 전이 가능성에 집중
        - 매칭 여부를 판단할 때 긍정적 편견을 가지고 분석

        *** 이력서 분석 데이터 ***
        이력서 요약:
        {json.dumps(resume_result, ensure_ascii=False, indent=2)}

        원본 이력서 전체 내용:
        {original_resume_text if original_resume_text else "원본 이력서 텍스트 미제공"}

        JD 정보:
        {json.dumps(jd_data, ensure_ascii=False, indent=2)}

        *** 분석 프로세스 ***
        1. JD의 각 요구사항을 하나씩 분석
        2. 이력서 요약과 원본 이력서 전체에서 해당 요구사항과 의미적으로 연관될 수 있는 모든 경험 탐색
        3. 직접적 매칭 + 간접적 매칭 + 전이 가능한 경험 모두 고려
        4. 조금이라도 관련성이 있다면 매칭으로 판단
        5. 매칭된 경우 구체적인 근거와 설명 제공

        반드시 아래 JSON 형식만 출력하세요:
        {{
            "step1_mapping": {{
                각_JD_요구사항에_대해: {{
                    "matched": true_or_false,
                    "resume_evidence": "이력서에서 찾은 관련 경험 (구체적으로)",
                    "explanation": "매칭 판단의 논리적 근거와 연관성 설명"
                }}
            }},
            "overall_score": 적절한_점수,
            "skill_match": {{
                "required_skills_score": 점수,
                "preferred_skills_score": 점수,
                "matched_skills": ["매칭된 요구사항들"],
                "missing_skills": ["정말로 관련 경험이 전혀 없는 요구사항들만"],
                "skill_mapping": {{
                    "매칭된_요구사항": "해당_이력서_경험"
                }}
            }},
            "experience_match": {{
                "score": 점수,
                "candidate_years": {resume_result.get('total_years', 0)},
                "required_years": {jd_data.get('required_experience', 0)},
                "assessment": "경력 수준 평가"
            }},
            "domain_match": {{
                "score": 점수,
                "assessment": "도메인 적합성 평가"
            }},
            "culture_match": {{
                "score": 점수,
                "assessment": "문화 적합성 평가"
            }},
            "strengths": ["매칭된 경험 기반 강점들"],
            "improvement_areas": ["실제로 부족한 영역만"],
            "recommendation": "균형있고 건설적인 추천 의견",
            "detailed_analysis": {{
                "resume_highlights": ["JD와 매칭되는 주요 경험들"],
                "jd_coverage": "JD 요구사항 커버리지 분석",
                "gap_analysis": "실제 보완 필요 영역"
            }}
        }}
        """

        logging.info("적극적 의미적 매칭 분석 API 호출 시작")
        logging.info(f"전체 프롬프트: {prompt}")
        
        print("GPT API 호출 시작 (적극적 의미적 매칭)...")
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert Korean HR analyst specializing in AGGRESSIVE SEMANTIC MATCHING. Your core principle: when in doubt, match it. Look for ANY possible connection between resume experiences and JD requirements. Be extremely generous and positive in recognizing relevant experience. Focus on transferable skills, related competencies, and the essence of work rather than exact keywords. If there's even 30% relevance, mark it as matched. Always respond in Korean. Be an advocate for the candidate."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3  # 창의적 연결을 위해 약간 높임
        )
        print("GPT API 호출 완료")

        result = response.choices[0].message.content.strip()
        
        # 디버깅: GPT 응답 전체 로깅
        print("=== GPT 응답 전체 ===")
        print(result)
        print("=" * 50)
        
        logging.info(f"적극적 의미적 매칭 분석 결과: {result}")
        
        # JSON 형식 정리 (더 강화된 파싱)
        cleaned_result = result.strip()
        
        # 코드 블록 제거
        if cleaned_result.startswith("```json"):
            cleaned_result = cleaned_result[7:]  # "```json" 제거
        elif cleaned_result.startswith("```"):
            cleaned_result = cleaned_result[3:]   # "```" 제거
            
        if cleaned_result.endswith("```"):
            cleaned_result = cleaned_result[:-3]  # 끝의 "```" 제거
            
        # JSON 시작과 끝 찾기
        start_idx = cleaned_result.find("{")
        end_idx = cleaned_result.rfind("}")
        
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            cleaned_result = cleaned_result[start_idx:end_idx+1]
        
        logging.info(f"정리된 JSON: {cleaned_result[:200]}...")
        
        # JSON 파싱
        try:
            parsed_result = json.loads(cleaned_result)
            logging.info("적극적 의미적 매칭 분석 성공적으로 파싱됨")
            return parsed_result
        except json.JSONDecodeError as json_err:
            logging.error(f"JSON 파싱 실패: {str(json_err)}")
            logging.error(f"원본 응답: {result}")
            logging.error(f"정리된 응답: {cleaned_result}")
            
            # JSON 파싱 실패 시 None 반환하여 에러 처리
            return None

    except Exception as e:
        logging.error(f"매칭 점수 계산 오류: {str(e)}")
        return None

def create_jd_analysis_blocks(jd_data, jd_name=None):
    """JD 분석 결과를 Slack 블록으로 변환"""
    title = f"📋 JD 분석 결과"
    if jd_name:
        title += f": {jd_name}"
    else:
        title += f": {jd_data.get('position', 'N/A')}"
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": title,
                "emoji": True
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🏢 도메인:* {jd_data.get('domain', 'N/A')}\n*📅 요구 경력:* {jd_data.get('required_experience', 'N/A')}년 이상"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*💻 필수 기술*"
            }
        }
    ]
    
    # 필수 기술
    for skill in jd_data.get('required_skills', []):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"• {skill}"
            }
        })
    
    # 우대 기술
    if jd_data.get('preferred_skills'):
        blocks.extend([
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*⭐ 우대 기술*"
                }
            }
        ])
        
        for skill in jd_data.get('preferred_skills', []):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• {skill}"
                }
            })
    
    # 주요 업무
    if jd_data.get('responsibilities'):
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🎯 주요 업무*"
                }
            }
        ])
        
        for responsibility in jd_data.get('responsibilities', []):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"• {responsibility}"
                }
            })
    
    blocks.extend([
        {
            "type": "divider"
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ JD 등록 완료! 이제 이력서를 업로드하면 매칭 분석을 진행합니다."
                }
            ]
        }
    ])
    
    return blocks

def create_matching_result_blocks(matching_result, jd_data, jd_name=None):
    """매칭 결과를 Slack 블록으로 변환 (이전 완벽 버전)"""
    overall_score = matching_result.get('overall_score', 0)
    
    # 점수에 따른 색상 결정
    if overall_score >= 80:
        color = "🟢"
        status = "매우 적합"
    elif overall_score >= 60:
        color = "🟡"
        status = "적합"
    elif overall_score >= 40:
        color = "🟠"
        status = "보통"
    else:
        color = "🔴"
        status = "부족"
    
    # 헤더 제목 설정
    header_title = "🎯 JD 매칭 분석 결과"
    if jd_name:
        header_title += f" ({jd_name})"
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_title,
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📊 전체 매칭 점수*\n{color} **{overall_score}점** ({status})"
            }
        },
        {
            "type": "divider"
        }
    ]
    
    # 세부 점수
    skill_match = matching_result.get('skill_match', {})
    experience_match = matching_result.get('experience_match', {})
    domain_match = matching_result.get('domain_match', {})
    
    blocks.append({
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": f"*💻 필수기술 매칭*\n{skill_match.get('required_skills_score', 0)}점"
            },
            {
                "type": "mrkdwn",
                "text": f"*📅 경력 매칭*\n{experience_match.get('score', 0)}점"
            },
            {
                "type": "mrkdwn",
                "text": f"*🏢 도메인 매칭*\n{domain_match.get('score', 0)}점"
            },
            {
                "type": "mrkdwn",
                "text": f"*⭐ 우대기술 매칭*\n{skill_match.get('preferred_skills_score', 0)}점"
            }
        ]
    })
    
    # 스킬 매핑 정보 (새로 추가)
    if skill_match.get('skill_mapping'):
        blocks.extend([
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🔍 상세 매칭 분석*"
                }
            }
        ])
        
        # step1_mapping 정보 표시 (더 투명한 분석)
        if matching_result.get('step1_mapping'):
            for jd_req, mapping_info in matching_result['step1_mapping'].items():
                status_icon = "✅" if mapping_info.get('matched') else "❌"
                evidence = mapping_info.get('resume_evidence', 'N/A')
                explanation = mapping_info.get('explanation', 'N/A')
                
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{status_icon} *{jd_req}*\n📋 증거: {evidence}\n💭 판단: {explanation}"
                    }
                })
        else:
            # 기존 skill_mapping 방식 (fallback)
            skill_mapping = skill_match.get('skill_mapping', {})
            for jd_requirement, resume_experience in skill_mapping.items():
                if jd_requirement != "JD요구사항":  # 헤더 제외
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"• *{jd_requirement}*\n  ✓ {resume_experience}"
                        }
                    })
    
    # 매칭된 기술
    if skill_match.get('matched_skills'):
        blocks.extend([
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*✅ 매칭된 기술*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• " + "\n• ".join(skill_match.get('matched_skills', []))
                }
            }
        ])
    
    # 부족한 기술 (정말 부족한 것만)
    if skill_match.get('missing_skills'):
        blocks.extend([
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*⚠️ 추가 보완 필요*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• " + "\n• ".join(skill_match.get('missing_skills', []))
                }
            }
        ])
    
    # 강점
    if matching_result.get('strengths'):
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🚀 이 포지션에서의 강점*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• " + "\n• ".join(matching_result.get('strengths', []))
                }
            }
        ])
    
    # 개선 영역
    if matching_result.get('improvement_areas'):
        blocks.extend([
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*📈 개선이 필요한 영역*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• " + "\n• ".join(matching_result.get('improvement_areas', []))
                }
            }
        ])
    
    # 상세 분석 (새로 추가)
    detailed_analysis = matching_result.get('detailed_analysis', {})
    if detailed_analysis:
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*📋 상세 분석*"
                }
            }
        ])
        
        if detailed_analysis.get('resume_highlights'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*주목할 만한 경험*\n• " + "\n• ".join(detailed_analysis.get('resume_highlights', []))
                }
            })
        
        if detailed_analysis.get('jd_coverage'):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*JD 커버리지*\n{detailed_analysis.get('jd_coverage', 'N/A')}"
                }
            })
    
    # 추천 의견
    if matching_result.get('recommendation'):
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*💡 추천 의견*\n_{matching_result.get('recommendation', 'N/A')}_"
                }
            }
        ])
    
    # PDF 다운로드 버튼 추가 (JD 매칭 분석용)
    blocks.extend([
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*📊 추가 액션*"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📄 PDF 보고서 다운로드",
                        "emoji": True
                    },
                    "action_id": "generate_pdf_report",
                    "style": "primary"
                }
            ]
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ JD 매칭 분석 완료! PDF 보고서에는 매칭 분석 결과가 포함됩니다."
                }
            ]
        }
    ])
    
    return blocks

def trigger_jd_registration(user_id):
    """JD 등록 프로세스를 시작하는 함수"""
    try:
        help_message = """
📋 **JD 등록 프로세스**

다음 단계로 JD를 등록해주세요:

1️⃣ **JD 이름을 입력하세요** (예: "채용담당자", "서비스기획자")
2️⃣ **채용공고 전문을 복사해서 보내주세요**

JD 이름을 먼저 보내주시면, 다음에 받는 메시지를 채용공고로 인식하여 분석합니다.

예시:
```
채용담당자
```
        """
        send_dm(user_id, help_message.strip())
        
        # 사용자 상태를 JD 등록 모드로 설정
        if user_id not in stored_jd:
            stored_jd[user_id] = {}
        stored_jd[user_id]["_registration_mode"] = "waiting_for_jd_name"
        
    except Exception as e:
        logging.error(f"JD 등록 트리거 오류: {str(e)}")
        send_dm(user_id, "❌ JD 등록 프로세스 시작 중 오류가 발생했습니다.")

def perform_complete_analysis(user_id, file_url, jd_name=None):
    """이력서 분석과 JD 매칭을 통합해서 수행하는 함수"""
    try:
        # 이력서 다운로드 및 텍스트 추출
        resume_text = download_resume(file_url)
        if not resume_text:
            send_dm(user_id, "❌ 이력서 다운로드에 실패했습니다.")
            return False
            
        # 이력서 분석
        parsed_result = analyze_resume(resume_text, user_id)
        if not parsed_result:
            send_dm(user_id, "❌ 이력서 분석에 실패했습니다.")
            return False
        
        # 전역 변수에 저장 (PDF 생성용)
        global last_analysis_result, last_analysis_user_id
        last_analysis_result = parsed_result
        last_analysis_user_id = user_id
        
        # 기술 스킬 차트 생성 및 업로드
        tech_skills = parsed_result.get("skill_cards", {}).get("tech_skills", "")
        skills_dict = parse_skills(tech_skills)
        
        if skills_dict:
            chart_bytes = create_plotly_radar_chart(skills_dict)
            if chart_bytes:
                try:
                    dm_response = client.conversations_open(users=[user_id])
                    if dm_response.get("ok"):
                        dm_channel_id = dm_response["channel"]["id"]
                        file_id = upload_image_to_slack(chart_bytes, "Technical Skills Radar Chart", dm_channel_id)
                        if not file_id:
                            logging.error("Failed to upload chart")
                except Exception as e:
                    logging.error(f"Chart upload error: {str(e)}")
        
        # 이력서 분석 결과 전송
        blocks = create_stat_card_blocks(parsed_result)
        send_dm(user_id, "✅ 이력서 분석이 완료되었습니다.", blocks=blocks)
        
        # JD 매칭 분석 (선택된 JD가 있는 경우)
        if jd_name and user_id in stored_jd and jd_name in stored_jd[user_id]:
            send_dm(user_id, f"🎯 **{jd_name}** JD와의 매칭 분석을 시작합니다...")
            
            jd_data = stored_jd[user_id][jd_name]
            matching_result = calculate_matching_score(parsed_result, jd_data, resume_text)
            
            if matching_result:
                matching_blocks = create_matching_result_blocks(matching_result, jd_data, jd_name)
                send_dm(user_id, f"🎯 **{jd_name}** JD 매칭 분석이 완료되었습니다!", blocks=matching_blocks)
            else:
                send_dm(user_id, "❌ JD 매칭 분석에 실패했습니다.")
        elif user_id in stored_jd:
            # JD는 있지만 선택되지 않은 경우 안내
            user_jds = [name for name in stored_jd[user_id].keys() if not name.startswith("_")]
            if user_jds:
                send_dm(user_id, f"💡 등록된 JD({', '.join(user_jds)})와의 매칭 분석을 원하시면 JD를 선택해서 분석해주세요.")
        
        return True
        
    except Exception as e:
        logging.error(f"Complete analysis error: {str(e)}")
        send_dm(user_id, f"❌ 분석 중 오류 발생: {str(e)}")
        return False

# Modal 대시보드 관련 함수들 추가
def get_all_resumes_from_notion():
    """Notion DB에서 모든 이력서 데이터를 가져오는 함수"""
    try:
        results = notion.databases.query(
            database_id=NOTION_DATABASE_ID
        )
        return results.get('results', [])
    except Exception as e:
        logging.error(f"Notion DB 조회 중 오류 발생: {str(e)}")
        return []

def parse_notion_resume_data(notion_pages):
    """Notion 페이지 데이터를 파싱하여 대시보드용 데이터로 변환"""
    parsed_data = []
    
    for page in notion_pages:
        try:
            properties = page.get('properties', {})
            
            # 기본 정보 추출
            name = "미기재"
            if properties.get('성명', {}).get('title'):
                name = properties['성명']['title'][0]['text']['content']
            
            # 경력 연차 추출
            years = 0
            career_text = ""
            if properties.get('경력기간', {}).get('rich_text'):
                career_text = properties['경력기간']['rich_text'][0]['text']['content']
                # 숫자 추출 (예: "6년" -> 6)
                years_match = re.search(r'(\d+)', career_text)
                if years_match:
                    years = int(years_match.group(1))
            
            # 매칭률 추출 (있는 경우)
            matching_score = 0
            if properties.get('매칭률', {}).get('number'):
                matching_score = properties['매칭률']['number']
            
            # 직무 분류 (키워드 기반으로 추정)
            job_category = "기타"
            strengths_text = ""
            if properties.get('강점 Top3', {}).get('rich_text'):
                strengths_text = properties['강점 Top3']['rich_text'][0]['text']['content']
                
            # 기술스택 추출
            tech_skills = ""
            if properties.get('기술스택', {}).get('rich_text'):
                tech_skills = properties['기술스택']['rich_text'][0]['text']['content']
            
            # 직무 분류 로직
            combined_text = (strengths_text + " " + tech_skills).lower()
            if any(keyword in combined_text for keyword in ['개발', 'developer', 'programming', 'coding', 'python', 'javascript', 'java']):
                job_category = "개발자"
            elif any(keyword in combined_text for keyword in ['기획', 'pm', 'product', 'manager', '프로덕트']):
                job_category = "PM/기획자"
            elif any(keyword in combined_text for keyword in ['디자인', 'design', 'ui', 'ux', 'figma']):
                job_category = "디자이너"
            elif any(keyword in combined_text for keyword in ['채용', 'hr', '인사', 'recruiting']):
                job_category = "HR/채용"
            elif any(keyword in combined_text for keyword in ['마케팅', 'marketing', '광고']):
                job_category = "마케팅"
            
            # 분석 일시
            created_time = page.get('created_time', '')
            
            parsed_data.append({
                'id': page['id'],
                'name': name,
                'years': years,
                'career_text': career_text,
                'job_category': job_category,
                'matching_score': matching_score,
                'strengths': strengths_text,
                'tech_skills': tech_skills,
                'created_time': created_time,
                'notion_url': f"https://www.notion.so/{page['id'].replace('-', '')}"
            })
            
        except Exception as e:
            logging.error(f"페이지 파싱 중 오류: {str(e)}")
            continue
    
    return parsed_data

def create_dashboard_modal():
    """대시보드 Modal UI 생성"""
    return {
        "type": "modal",
        "callback_id": "dashboard_modal",
        "title": {
            "type": "plain_text",
            "text": "📊 이력서 대시보드"
        },
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*이력서 분석 결과를 한눈에 확인하세요!* 🚀"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "input",
                "block_id": "job_filter",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "직무 선택"
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "전체"
                            },
                            "value": "all"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "개발자"
                            },
                            "value": "developer"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "PM/기획자"
                            },
                            "value": "pm"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "디자이너"
                            },
                            "value": "designer"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "HR/채용"
                            },
                            "value": "hr"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "마케팅"
                            },
                            "value": "marketing"
                        }
                    ],
                    "action_id": "select_job"
                },
                "label": {
                    "type": "plain_text",
                    "text": "직무 필터"
                }
            },
            {
                "type": "input",
                "block_id": "years_filter",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "경력 선택"
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "전체"
                            },
                            "value": "all"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "신입 (0-1년)"
                            },
                            "value": "0-1"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "주니어 (2-3년)"
                            },
                            "value": "2-3"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "미드레벨 (4-6년)"
                            },
                            "value": "4-6"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "시니어 (7년+)"
                            },
                            "value": "7+"
                        }
                    ],
                    "action_id": "select_years"
                },
                "label": {
                    "type": "plain_text",
                    "text": "경력 필터"
                }
            },
            {
                "type": "input",
                "block_id": "sort_filter",
                "element": {
                    "type": "static_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "정렬 기준"
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "최신순"
                            },
                            "value": "latest"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "매칭률 높은순"
                            },
                            "value": "matching_desc"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "경력 높은순"
                            },
                            "value": "years_desc"
                        },
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "이름순"
                            },
                            "value": "name"
                        }
                    ],
                    "action_id": "select_sort"
                },
                "label": {
                    "type": "plain_text",
                    "text": "정렬 순서"
                }
            },
            {
                "type": "section",
                "block_id": "loading_section",
                "text": {
                    "type": "mrkdwn",
                    "text": "📊 데이터를 불러오는 중..."
                }
            }
        ],
        "submit": {
            "type": "plain_text",
            "text": "필터 적용"
        },
        "close": {
            "type": "plain_text",
            "text": "닫기"
        }
    }

def apply_filters(data, job_filter="all", years_filter="all", sort_filter="latest"):
    """필터링 및 정렬 적용"""
    filtered_data = data.copy()
    
    # 직무 필터
    if job_filter != "all":
        job_mapping = {
            "developer": "개발자",
            "pm": "PM/기획자", 
            "designer": "디자이너",
            "hr": "HR/채용",
            "marketing": "마케팅"
        }
        target_job = job_mapping.get(job_filter, job_filter)
        filtered_data = [item for item in filtered_data if item['job_category'] == target_job]
    
    # 경력 필터
    if years_filter != "all":
        if years_filter == "0-1":
            filtered_data = [item for item in filtered_data if item['years'] <= 1]
        elif years_filter == "2-3":
            filtered_data = [item for item in filtered_data if 2 <= item['years'] <= 3]
        elif years_filter == "4-6":
            filtered_data = [item for item in filtered_data if 4 <= item['years'] <= 6]
        elif years_filter == "7+":
            filtered_data = [item for item in filtered_data if item['years'] >= 7]
    
    # 정렬
    if sort_filter == "latest":
        filtered_data.sort(key=lambda x: x['created_time'], reverse=True)
    elif sort_filter == "matching_desc":
        filtered_data.sort(key=lambda x: x['matching_score'], reverse=True)
    elif sort_filter == "years_desc":
        filtered_data.sort(key=lambda x: x['years'], reverse=True)
    elif sort_filter == "name":
        filtered_data.sort(key=lambda x: x['name'])
    
    return filtered_data

def create_dashboard_chart(data):
    """대시보드용 차트 생성"""
    if not data:
        return None
        
    try:
        # 매칭률 분포 히스토그램 생성
        matching_scores = [item['matching_score'] for item in data if item['matching_score'] > 0]
        
        if not matching_scores:
            return None
            
        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=matching_scores,
            nbinsx=10,
            name="매칭률 분포",
            marker_color='lightblue',
            opacity=0.7
        ))
        
        fig.update_layout(
            title="📊 매칭률 분포",
            xaxis_title="매칭률 (%)",
            yaxis_title="인원 수",
            width=800,
            height=400,
            font=dict(family="Malgun Gothic", size=12),
            paper_bgcolor='white',
            plot_bgcolor='white',
            showlegend=False
        )
        
        # PNG로 변환
        img_bytes = fig.to_image(format="png")
        return img_bytes
        
    except Exception as e:
        logging.error(f"차트 생성 중 오류: {str(e)}")
        return None

def create_filtered_results_blocks(data, total_count):
    """필터링된 결과를 Slack 블록으로 변환"""
    blocks = []
    
    # 요약 섹션
    blocks.extend([
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📋 *검색 결과: {len(data)}명* (전체 {total_count}명 중)"
            }
        },
        {
            "type": "divider"
        }
    ])
    
    # 통계 요약
    if data:
        avg_years = sum(item['years'] for item in data) / len(data)
        avg_matching = sum(item['matching_score'] for item in data if item['matching_score'] > 0)
        if avg_matching > 0:
            avg_matching = avg_matching / len([item for item in data if item['matching_score'] > 0])
        
        # 직무별 분포
        job_counts = {}
        for item in data:
            job = item['job_category']
            job_counts[job] = job_counts.get(job, 0) + 1
        
        job_summary = ", ".join([f"{job}: {count}명" for job, count in job_counts.items()])
        
        blocks.extend([
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*📈 평균 경력*\n{avg_years:.1f}년"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*🎯 평균 매칭률*\n{avg_matching:.1f}%" if avg_matching > 0 else "*🎯 평균 매칭률*\nN/A"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*👥 직무별 분포*\n{job_summary}"
                }
            },
            {
                "type": "divider"
            }
        ])
    
    # 개별 이력서 목록 (최대 10개)
    for i, item in enumerate(data[:10]):
        matching_emoji = "🟢" if item['matching_score'] >= 80 else "🟡" if item['matching_score'] >= 60 else "🔴" if item['matching_score'] > 0 else "⚪"
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{item['name']}* {matching_emoji}\n"
                        f"📋 {item['job_category']} • 📅 {item['years']}년차"
                        + (f" • 🎯 {item['matching_score']}%" if item['matching_score'] > 0 else "")
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📄 상세보기"
                },
                "url": item['notion_url'],
                "action_id": f"view_resume_{item['id']}"
            }
        })
    
    # 더 많은 결과가 있는 경우
    if len(data) > 10:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"... 외 {len(data) - 10}명 더 있습니다."
                }
            ]
        })
    
    return blocks

# 슬래시 명령어 전용 엔드포인트 추가
@app.route("/slack/commands", methods=["POST"])
def slack_commands():
    try:
        command = request.form.get("command")
        user_id = request.form.get("user_id")
        trigger_id = request.form.get("trigger_id")
        
        logging.info(f"슬래시 커맨드 수신: {command}, user_id: {user_id}, trigger_id: {trigger_id}")
        
        if command == "/dashboard":
            logging.info("=== 이력서 대시보드 명령어 처리 시작 ===")
            
            if not trigger_id:
                return jsonify({"text": "❌ trigger_id가 없습니다."}), 200
            
            try:
                # 기존 이력서 관리 대시보드 모달 열기
                dashboard_modal = create_dashboard_modal()
                
                response = client.views_open(
                    trigger_id=trigger_id,
                    view=dashboard_modal
                )
                
                if not response.get("ok"):
                    logging.error(f"대시보드 모달 열기 실패: {response}")
                    return jsonify({"text": f"❌ 모달 열기 실패: {response.get('error', '알 수 없음')}"}), 200
                
                logging.info("이력서 대시보드 모달 열기 성공")
                return "", 200
                
            except Exception as e:
                logging.error(f"이력서 대시보드 모달 처리 오류: {str(e)}", exc_info=True)
                return jsonify({"text": f"❌ 대시보드 생성 중 오류가 발생했습니다: {str(e)}"}), 200
        
        elif command == '/market':
            logging.info("=== 시장 인텔리전스 명령어 처리 시작 ===")
            
            if not trigger_id:
                return jsonify({"text": "❌ trigger_id가 없습니다."}), 200
            
            try:
                # 즉시 로딩 모달 표시
                loading_modal = {
                    "type": "modal",
                    "callback_id": "market_loading",
                    "title": {
                        "type": "plain_text",
                        "text": "📊 채용 시장 인텔리전스"
                    },
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "🔄 *실시간 채용 시장 데이터 수집 중...*\n\n• 원티드 채용공고 스크래핑 중\n• 기업별 통계 분석 중\n• 기술스택 트렌드 분석 중\n\n잠시만 기다려주세요! ⏳"
                            }
                        }
                    ],
                    "close": {
                        "type": "plain_text",
                        "text": "취소"
                    }
                }
                
                # 로딩 모달 열기
                response = client.views_open(
                    trigger_id=trigger_id,
                    view=loading_modal
                )
                
                if not response.get("ok"):
                    logging.error(f"로딩 모달 열기 실패: {response}")
                    return jsonify({"text": f"❌ 모달 열기 실패: {response.get('error', '알 수 없음')}"}), 200
                
                view_id = response["view"]["id"]
                logging.info(f"로딩 모달 열기 성공, view_id: {view_id}")
                
                # 백그라운드에서 실제 데이터 처리
                def process_market_modal():
                    try:
                        logging.info("백그라운드 시장 인텔리전스 데이터 처리 시작")
                        
                        # 실제 스크래핑 시도
                        try:
                            scraped_jobs = scrape_wanted_jobs()
                            if scraped_jobs and len(scraped_jobs) > 0:
                                logging.info(f"실제 스크래핑 성공: {len(scraped_jobs)}개 공고")
                                analyzed_data = analyze_scraped_data(scraped_jobs)
                                data_source = "실시간 데이터"
                            else:
                                logging.warning("스크래핑 결과 없음, 목업 데이터 사용")
                                analyzed_data = get_mock_data()
                                data_source = "데모 데이터"
                        except Exception as e:
                            logging.error(f"스크래핑 실패: {str(e)}")
                            analyzed_data = get_mock_data()
                            data_source = "데모 데이터"
                        
                        # 시장 인텔리전스 모달 생성
                        market_modal = create_market_intelligence_modal_with_data(analyzed_data, data_source)
                        
                        # 모달 업데이트
                        update_response = client.views_update(
                            view_id=view_id,
                            view=market_modal
                        )
                        
                        if update_response.get("ok"):
                            logging.info("시장 인텔리전스 모달 업데이트 성공")
                        else:
                            logging.error(f"모달 업데이트 실패: {update_response}")
                            
                    except Exception as e:
                        logging.error(f"백그라운드 시장 인텔리전스 처리 오류: {str(e)}", exc_info=True)
                        
                        # 에러 모달 표시
                        error_modal = {
                            "type": "modal",
                            "callback_id": "market_error",
                            "title": {
                                "type": "plain_text",
                                "text": "❌ 오류 발생"
                            },
                            "blocks": [
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*시장 인텔리전스 생성 중 오류가 발생했습니다.*\n\n```{str(e)}```\n\n잠시 후 다시 시도해주세요."
                                    }
                                }
                            ],
                            "close": {
                                "type": "plain_text",
                                "text": "닫기"
                            }
                        }
                        
                        try:
                            client.views_update(view_id=view_id, view=error_modal)
                        except:
                            pass
                
                # 백그라운드 스레드 시작
                import threading
                thread = threading.Thread(target=process_market_modal)
                thread.daemon = True
                thread.start()
                
                return "", 200
                
            except Exception as e:
                logging.error(f"시장 인텔리전스 모달 처리 오류: {str(e)}", exc_info=True)
                return jsonify({"text": f"❌ 시장 인텔리전스 생성 중 오류가 발생했습니다: {str(e)}"}), 200
        
        return "알 수 없는 명령어입니다.", 200
        
    except Exception as e:
        logging.error(f"슬래시 커맨드 전체 오류: {str(e)}", exc_info=True)
        return f"명령어 처리 중 오류가 발생했습니다: {str(e)}", 200

# 경쟁사 채용 현황 분석 관련 함수들
def get_competitor_hiring_data(force_scraping=False):
    """경쟁사 채용 현황 - 선택적 스크래핑"""
    if force_scraping:
        # 새로고침 버튼 클릭 시에만 실제 스크래핑
        try:
            logging.info("강제 스크래핑 모드: 실제 원티드 데이터 수집")
            scraped_jobs = scrape_wanted_jobs()
            
            if scraped_jobs:
                logging.info(f"스크래핑 성공: {len(scraped_jobs)}개 공고")
                return analyze_scraped_data(scraped_jobs)
            else:
                logging.warning("스크래핑 데이터가 없어 목업 데이터 사용")
                return get_mock_data()
                
        except Exception as e:
            logging.error(f"스크래핑 중 오류 발생: {str(e)}, 목업 데이터 사용")
            return get_mock_data()
    else:
        # 기본적으로는 빠른 목업 데이터 사용
        logging.info("기본 모드: 목업 데이터 사용 (빠른 응답)")
        return get_mock_data()

def get_mock_data():
    """목업 데이터 (스크래핑 실패 시 폴백)"""
    return {
        "last_updated": "2025-06-04 21:30",
        "total_jobs": 1247,
        "growth_rate": 18,  # 전월 대비 증가율
        "companies": [
            {
                "name": "삼성전자",
                "jobs_count": 23,
                "change": 5,
                "trend": "up",
                "hot_positions": ["클라우드 엔지니어", "AI 연구원", "백엔드 개발자"],
                "avg_salary": "5200만원",
                "logo_emoji": "📱"
            },
            {
                "name": "네이버",
                "jobs_count": 18,
                "change": 0,
                "trend": "stable",
                "hot_positions": ["프론트엔드 개발자", "데이터 사이언티스트", "UX 디자이너"],
                "avg_salary": "4800만원",
                "logo_emoji": "🟢"
            },
            {
                "name": "카카오",
                "jobs_count": 15,
                "change": -2,
                "trend": "down",
                "hot_positions": ["iOS 개발자", "게임 개발자", "DevOps 엔지니어"],
                "avg_salary": "4600만원",
                "logo_emoji": "💬"
            },
            {
                "name": "토스",
                "jobs_count": 31,
                "change": 12,
                "trend": "hot",
                "hot_positions": ["풀스택 개발자", "보안 엔지니어", "프로덕트 매니저"],
                "avg_salary": "5500만원",
                "logo_emoji": "💳"
            },
            {
                "name": "쿠팡",
                "jobs_count": 27,
                "change": 8,
                "trend": "up",
                "hot_positions": ["데이터 엔지니어", "ML 엔지니어", "시스템 엔지니어"],
                "avg_salary": "5000만원",
                "logo_emoji": "📦"
            },
            {
                "name": "배달의민족",
                "jobs_count": 19,
                "change": 3,
                "trend": "up",
                "hot_positions": ["백엔드 개발자", "안드로이드 개발자", "QA 엔지니어"],
                "avg_salary": "4700만원",
                "logo_emoji": "🍔"
            }
        ],
        "insights": [
            "토스가 대규모 채용 중! 핀테크 분야 경쟁 심화 예상",
            "DevOps/클라우드 엔지니어 수요가 전 업계에서 급증",
            "평균 연봉이 전월 대비 7% 상승, 인재 확보 경쟁 치열"
        ],
        "hot_skills": [
            {"skill": "Kubernetes", "growth": 25, "companies": 8},
            {"skill": "React", "growth": 15, "companies": 12},
            {"skill": "Python", "growth": 12, "companies": 15},
            {"skill": "AWS", "growth": 20, "companies": 10},
            {"skill": "TypeScript", "growth": 18, "companies": 9}
        ]
    }

def create_market_intelligence_modal(force_scraping=False, user_id=None):
    """채용 시장 인텔리전스 Modal 생성 (기술스택 분포 차트 포함)"""
    data = get_competitor_hiring_data(force_scraping=force_scraping)
    blocks = []
    # ... 기존 헤더/통계 블록 ...
    header_text = "🏢 경쟁사 채용 현황 분석"
    if force_scraping:
        header_text += " (실시간 데이터)"
    else:
        header_text += " (Demo 데이터)"
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text, "emoji": True}
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*📊 전체 시장 현황*\n• 총 공고 수: *{data['total_jobs']:,}개* (↑{data['growth_rate']}%)\n• 마지막 업데이트: {data['last_updated']}"}
    })
    blocks.append({"type": "divider"})
    # ... 경쟁사별 현황 ...
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*🏢 주요 기업 채용 현황*"}
    })
    for company in data['companies']:
        trend_emoji = "🔥" if company['trend'] == "hot" else "📈" if company['trend'] == "up" else "📊" if company['trend'] == "stable" else "📉"
        change_text = f"↑{company['change']}" if company['change'] > 0 else f"↓{abs(company['change'])}" if company['change'] < 0 else "→"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                "text": f"{company['logo_emoji']} *{company['name']}* {trend_emoji}\n"
                        f"📋 {company['jobs_count']}개 공고 ({change_text}개)\n"
                        f"💰 평균 연봉: {company['avg_salary']}\n"
                        f"🔥 인기 포지션: {', '.join(company['hot_positions'][:2])}"
            }
        })
    # 기술스택 분포 차트 생성 및 DM 전송 (안전성을 위해 비활성화)
    try:
        logging.info("차트 생성 시작")
        # 차트 생성을 비활성화하여 안정성 확보
        # TODO: 차트 기능은 별도 스레드나 비동기로 처리 필요
        logging.info("차트 생성 완료 (안정성을 위해 비활성화됨)")
    except Exception as e:
        logging.error(f"기술스택 차트 생성/업로드 오류: {str(e)}", exc_info=True)
    # 모달에 안내 텍스트 block 추가
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "🔥 *기술스택 분포 차트는 DM(Direct Message)으로 전송되었습니다.*"}
    })
    # 핫한 기술스택
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*🔥 급상승 기술스택 TOP 5*"}
    })
    for i, skill in enumerate(data['hot_skills'][:5], 1):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{i}. {skill['skill']}* (↑{skill['growth']}%)\\n{skill['companies']}개 기업에서 채용 중"}
        })
    # ... 이하 기존 인사이트, 새로고침 버튼 등 ...
    blocks.extend([
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*💡 시장 인사이트*"}},
    ])
    for insight in data['insights']:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"• {insight}"}})
    blocks.extend([
        {"type": "divider"},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "🔄 데이터 새로고침", "emoji": True}, "action_id": "refresh_market_data", "style": "primary"}
        ]}
    ])
    return {
        "type": "modal",
        "callback_id": "market_intelligence_modal",
        "title": {"type": "plain_text", "text": "📊 채용 시장 인텔리전스"},
        "blocks": blocks,
        "close": {"type": "plain_text", "text": "닫기"}
    }

# 실제 원티드 스크래핑 함수들
def scrape_wanted_jobs():
    """원티드에서 IT 채용공고 실제 스크래핑 (Selenium 사용) - 개선된 셀렉터"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = None
    jobs_data = []
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        logging.info("Chrome 브라우저 시작됨")
        
        # 원티드 개발자 채용공고 페이지
        url = "https://www.wanted.co.kr/wdlist/518?country=kr&job_sort=job.latest_order&years=-1&locations=all"
        driver.get(url)
        logging.info(f"페이지 로드 완료: {url}")
        
        # 페이지 로딩 대기
        time.sleep(5)
        
        # 실제로 작동하는 셀렉터 사용: 채용공고 링크들 찾기
        job_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/wd/']")
        logging.info(f"발견된 채용공고 링크 수: {len(job_links)}")
        
        if not job_links:
            logging.warning("채용공고 링크를 찾을 수 없음")
            return []
        
        # 각 채용공고에서 데이터 추출
        for index, link in enumerate(job_links[:20]):  # 최대 20개
            try:
                # 전체 텍스트에서 정보 추출
                full_text = link.text.strip()
                if not full_text or len(full_text) < 10:
                    continue
                
                logging.info(f"공고 {index+1} 텍스트: {full_text[:100]}...")
                
                # 텍스트 파싱으로 회사명과 포지션 추출
                lines = [line.strip() for line in full_text.split('\n') if line.strip()]
                
                company_name = None
                position_name = None
                
                # 텍스트 라인 분석
                for line in lines:
                    # 회사명 찾기 (한글 + 영문 + 괄호 조합, 보통 짧음)
                    if not company_name and 2 <= len(line) <= 30:
                        # 포지션명이 아닌 것 같은 것들
                        if not any(keyword in line.lower() for keyword in 
                            ['developer', '개발자', 'engineer', '엔지니어', 'backend', 'frontend', 
                             'fullstack', 'react', 'java', 'python', 'php', '합격보상금', '100만원']):
                            company_name = line
                    
                    # 포지션명 찾기 (개발 관련 키워드 포함)
                    if not position_name and 5 <= len(line) <= 80:
                        if any(keyword in line.lower() for keyword in 
                            ['developer', '개발자', 'engineer', '엔지니어', 'backend', 'frontend', 
                             'fullstack', 'react', 'vue', 'angular', 'java', 'python', 'php', 
                             'javascript', 'kotlin', 'swift', 'node', 'spring', 'django']):
                            position_name = line
                
                # 기본값 설정
                if not company_name:
                    # 마지막에서 두 번째 라인이 회사명일 가능성
                    if len(lines) >= 2:
                        company_name = lines[-2] if lines[-2] and '합격보상금' not in lines[-2] else "알 수 없음"
                    else:
                        company_name = "알 수 없음"
                
                if not position_name:
                    # 첫 번째나 두 번째 라인에서 포지션명 찾기
                    for line in lines[:3]:
                        if '합격보상금' not in line and len(line) > 5:
                            position_name = line
                            break
                    if not position_name:
                        position_name = "개발자"
                
                # 기술스택 추출
                tech_skills = extract_tech_skills_from_text(full_text)
                
                # 결과 저장
                job_data = {
                    'company': company_name,
                    'position': position_name,
                    'tech_skills': tech_skills
                }
                
                jobs_data.append(job_data)
                logging.info(f"추출 완료 {index+1}: {company_name} | {position_name} | {tech_skills}")
                
            except Exception as e:
                logging.error(f"채용공고 {index+1} 처리 실패: {str(e)}")
                continue
        
        logging.info(f"총 {len(jobs_data)}개 채용공고 스크래핑 완료")
        return jobs_data
        
    except Exception as e:
        logging.error(f"스크래핑 오류: {str(e)}", exc_info=True)
        return []
        
    finally:
        if driver:
            driver.quit()
            logging.info("브라우저 종료됨")

def extract_tech_skills_from_text(text):
    """텍스트에서 실제 기술스택만 추출 - 완전히 개선된 버전"""
    if not text:
        return []
    
    # 실제 기술스택 키워드 목록 (정확한 매칭을 위해 세분화)
    tech_keywords = {
        # 프로그래밍 언어
        'python', 'java', 'javascript', 'typescript', 'kotlin', 'swift', 'go', 'rust',
        'c++', 'c#', 'php', 'ruby', 'scala', 'r', 'dart', 'objective-c',
        
        # 웹 프론트엔드
        'react', 'vue', 'angular', 'svelte', 'next.js', 'nuxt.js', 'gatsby',
        'jquery', 'bootstrap', 'tailwind', 'sass', 'less', 'webpack', 'vite',
        
        # 백엔드 프레임워크
        'spring', 'django', 'flask', 'fastapi', 'express', 'nest.js', 'laravel',
        'rails', 'asp.net', 'gin', 'echo', 'fiber',
        
        # 데이터베이스
        'mysql', 'postgresql', 'mongodb', 'redis', 'elasticsearch', 'oracle',
        'sqlite', 'mariadb', 'cassandra', 'dynamodb', 'neo4j', 'influxdb',
        
        # 클라우드 & 인프라
        'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'jenkins', 'gitlab-ci',
        'github-actions', 'terraform', 'ansible', 'vagrant', 'nginx', 'apache',
        
        # 데이터 & AI/ML
        'tensorflow', 'pytorch', 'pandas', 'numpy', 'scikit-learn', 'spark',
        'hadoop', 'kafka', 'airflow', 'jupyter', 'tableau', 'power-bi',
        
        # 모바일
        'android', 'ios', 'flutter', 'react-native', 'xamarin', 'ionic',
        
        # 기타 도구 & 기술
        'git', 'jira', 'confluence', 'figma', 'sketch', 'linux', 'ubuntu',
        'graphql', 'rest', 'grpc', 'microservices', 'serverless'
    }
    
    # 제외할 키워드 (더 포괄적으로)
    exclude_keywords = {
        # 회사명 관련
        '합격보상금', '100만원', '보상금', '지란지교데이터', '그래픽', '위대한상상', 
        '요기요', '바로고', 'barogo', '원티드', '나니아랩스', '더블미디어', 
        '이스트소프트', 'estsoft', '여기어때컴퍼니', '엘박스', '칩스앤미디어',
        '메가스터디교육', '불마켓랩스',
        
        # 지역명
        '서울', '경기', '강남구', '서초구', '용산구', '강서구', '성남시', '판교',
        '부산', '대구', '인천', '광주', '대전', '울산', '세종',
        
        # 일반 단어
        '신입', '경력', '년', '이상', '개발자', 'engineer', 'developer', '담당자',
        'application', 'product', 'sres', 'apps', '근무지', '일본', '주식회사',
        '채용', '공고', '포지션', '업무', '담당', '관리', '운영', '기획', '설계',
        '분석', '구축', '개발', '유지보수', '최적화', '성능', '품질', '테스트',
        
        # 기타 불필요한 단어
        'front-end', 'back-end', 'full-stack', 'devops', 'qa', 'ui', 'ux',
        'pm', 'po', 'scrum', 'agile', 'team', 'lead', 'senior', 'junior'
    }
    
    found_skills = []
    text_lower = text.lower()
    
    # 정확한 기술스택 키워드만 찾기
    for keyword in tech_keywords:
        # 단어 경계를 고려한 정확한 매칭
        import re
        
        # 특수 문자가 포함된 키워드 처리 (예: next.js, c++)
        escaped_keyword = re.escape(keyword)
        
        # 단어 경계 패턴 생성
        patterns = [
            rf'\b{escaped_keyword}\b',  # 기본 단어 경계
            rf'(?<!\w){escaped_keyword}(?!\w)',  # 더 엄격한 경계
            rf'(?<![a-zA-Z]){escaped_keyword}(?![a-zA-Z])'  # 알파벳 경계만
        ]
        
        for pattern in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                # 제외 키워드와 겹치지 않는지 확인
                if not any(exclude in keyword.lower() for exclude in exclude_keywords):
                    found_skills.append(keyword.title())
                    break
    
    # 중복 제거 및 정렬
    unique_skills = sorted(list(set(found_skills)))
    
    # 로깅으로 디버깅
    if unique_skills:
        logging.info(f"추출된 기술스택: {unique_skills} (원본: {text[:100]}...)")
    
    return unique_skills

def analyze_scraped_data(jobs_data):
    """스크래핑된 데이터 분석 - 기술스택 필터링 강화 버전"""
    if not jobs_data:
        return get_mock_data()
    
    logging.info(f"분석할 데이터: {len(jobs_data)}개 공고")
    
    # Counter 사용으로 변경
    from collections import Counter
    
    company_counts = Counter()
    skill_counts = Counter()
    position_counts = Counter()
    
    for job in jobs_data:
        company = job.get('company', '알 수 없음')
        position = job.get('position', '개발자')
        tech_skills = job.get('tech_skills', [])
        
        # 회사별 카운트
        if company != '알 수 없음':
            company_counts[company] += 1
        
        # 포지션별 카운트 (실제 포지션만)
        if position and position != '개발자' and len(position) < 100:
            # 불필요한 텍스트가 포함되지 않은 실제 포지션만
            if not any(exclude in position.lower() for exclude in 
                      ['합격보상금', '100만원', '서울', '경기', '강남구', '서초구']):
                position_counts[position] += 1
        
        # 기술스택 카운트 (실제 기술만)
        for skill in tech_skills:
            if skill and len(skill) > 1:  # 빈 문자열이나 너무 짧은 것 제외
                # 실제 기술인지 한번 더 검증
                if is_valid_tech_skill(skill):
                    skill_counts[skill] += 1
    
    # 상위 회사들 (최소 1개 이상 공고)
    top_companies = company_counts.most_common(6)
    
    # 상위 기술스택들 (실제 기술만, 최소 1개 이상)
    top_skills = skill_counts.most_common(5)
    
    # 상위 포지션들
    top_positions = position_counts.most_common(5)
    
    logging.info(f"상위 회사: {top_companies}")
    logging.info(f"상위 기술스택: {top_skills}")
    logging.info(f"상위 포지션: {top_positions}")
    
    # 분석 결과 구성
    companies_data = []
    for company, count in top_companies:
        # 해당 회사의 포지션들 찾기 (상위 3개까지)
        company_jobs = [job for job in jobs_data if job.get('company') == company]
        top_positions = []
        if company_jobs:
            positions = [job.get('position', '개발자') for job in company_jobs]
            logging.info(f"[디버그] {company} 원본 포지션들: {positions}")
            
            # 실제 포지션만 필터링 (개발자도 허용하되 더 구체적인 것 우선)
            valid_positions = []
            specific_positions = []  # 구체적인 포지션들
            generic_positions = []   # '개발자' 같은 일반적인 포지션들
            
            for pos in positions:
                if pos and len(pos) < 100 and not any(exclude in pos.lower() for exclude in 
                    ['합격보상금', '100만원', '서울', '경기', '판교', '근무지', '신입', '경력', '채용']):
                    
                    # 더 구체적인 포지션인지 확인
                    if any(keyword in pos.lower() for keyword in 
                        ['프론트엔드', '백엔드', '풀스택', '모바일', 'frontend', 'backend', 'fullstack', 
                         'react', 'vue', 'angular', 'java', 'python', 'ios', 'android', 'devops', 
                         '데이터', 'ml', 'ai', '머신러닝', '인공지능', 'software', 'senior', 'junior']):
                        specific_positions.append(pos)
                    elif pos == '개발자':
                        generic_positions.append(pos)
                    else:
                        valid_positions.append(pos)
            
            # 우선순위: 구체적인 포지션 > 일반 포지션 > '개발자'
            all_positions = specific_positions + valid_positions + generic_positions
            logging.info(f"[디버그] {company} 필터링된 포지션들: {all_positions}")
            
            if all_positions:
                position_freq = Counter(all_positions)
                # 상위 3개 포지션 가져오기
                top_positions = [pos for pos, _ in position_freq.most_common(3)]
            
            # 포지션이 없으면 기본값
            if not top_positions:
                top_positions = ["개발자"]
                
            logging.info(f"[디버그] {company} 최종 선택된 포지션들: {top_positions}")
        else:
            top_positions = ["개발자"]
        
        companies_data.append({
            "name": company,
            "jobs_count": count,
            "trend": "+2",  # 임시 트렌드
            "top_positions": top_positions  # 여러 포지션 저장
        })
    
    # 기술스택 데이터 (실제 기술만)
    skills_data = []
    for skill, count in top_skills:
        if is_valid_tech_skill(skill):  # 한번 더 검증
            skills_data.append({
                "name": skill,
                "growth": f"+{min(30, count * 10)}%",  # 성장률 계산
                "companies_using": count
            })
    
    # 기술스택이 없으면 기본값 제공
    if not skills_data:
        skills_data = [
            {"name": "JavaScript", "growth": "+15%", "companies_using": 3},
            {"name": "Python", "growth": "+12%", "companies_using": 2},
            {"name": "React", "growth": "+18%", "companies_using": 2}
        ]
    
    return {
        "total_jobs": len(jobs_data),
        "growth_rate": "+15%",
        "last_updated": "2025-06-16 14:35",
        "companies": companies_data,
        "skills": skills_data,
        "insights": generate_insights(jobs_data, skill_counts, company_counts)
    }

def is_valid_tech_skill(skill):
    """기술스택이 실제 기술인지 검증하는 함수"""
    if not skill or len(skill) < 2 or len(skill) > 30:
        return False
    
    # 실제 기술 키워드 목록 (소문자)
    valid_tech_keywords = {
        'python', 'java', 'javascript', 'typescript', 'kotlin', 'swift', 'go', 'rust',
        'c++', 'c#', 'php', 'ruby', 'scala', 'r', 'dart',
        'react', 'vue', 'angular', 'svelte', 'next.js', 'nuxt.js',
        'spring', 'django', 'flask', 'fastapi', 'express', 'laravel',
        'mysql', 'postgresql', 'mongodb', 'redis', 'elasticsearch',
        'aws', 'azure', 'gcp', 'docker', 'kubernetes', 'jenkins',
        'tensorflow', 'pytorch', 'pandas', 'numpy', 'spark',
        'android', 'ios', 'flutter', 'react-native',
        'git', 'linux', 'ubuntu', 'nginx', 'apache'
    }
    
    # 제외할 키워드
    invalid_keywords = {
        '합격보상금', '100만원', '서울', '경기', '강남구', '서초구', '성남시',
        '신입', '경력', '년', '이상', '개발자', 'engineer', 'developer',
        '회사', '기업', '채용', '공고', '포지션'
    }
    
    skill_lower = skill.lower()
    
    # 제외 키워드 체크
    for invalid in invalid_keywords:
        if invalid.lower() in skill_lower:
            return False
    
    # 실제 기술 키워드 체크
    for valid_tech in valid_tech_keywords:
        if valid_tech in skill_lower:
            return True
    
    return False

def get_company_emoji(company_name):
    """회사명에 따른 이모지 반환"""
    emoji_map = {
        '삼성': '📱', '네이버': '🟢', '카카오': '💬', '토스': '💳',
        '쿠팡': '📦', '배달의민족': '🍔', '라인': '💚', 'SK': '🔴',
        '현대': '🚗', 'LG': '📺', '우아한형제들': '🍔'
    }
    
    for keyword, emoji in emoji_map.items():
        if keyword in company_name:
            return emoji
    
    return '🏢'  # 기본 이모지

def generate_insights(jobs_data, skill_counts, company_counts):
    """수집된 데이터를 바탕으로 인사이트 생성 - 수정된 버전"""
    from collections import Counter
    
    insights = []
    
    try:
        # dict를 Counter로 변환
        if isinstance(skill_counts, dict):
            skill_counts = Counter(skill_counts)
        if isinstance(company_counts, dict):
            company_counts = Counter(company_counts)
        
        # 실제 기술스택만 필터링하는 함수
        def is_real_tech(skill):
            if not skill or len(skill) > 30:
                return False
            
            exclude_words = ['합격보상금', '100만원', '서울', '경기', '강남구', '서초구', 
                           '성남시', '경력', '신입', '년', '이상', '개발자', '엔지니어', 
                           '담당자', '판교', '근무지', '일본', '株式会社']
            
            skill_lower = skill.lower()
            for word in exclude_words:
                if word.lower() in skill_lower:
                    return False
            
            # 실제 기술 키워드 확인
            tech_words = ['javascript', 'python', 'java', 'react', 'vue', 'angular', 
                         'node', 'spring', 'django', 'mysql', 'postgresql', 'mongodb',
                         'aws', 'azure', 'docker', 'kubernetes', 'typescript', 'php']
            
            for tech in tech_words:
                if tech in skill_lower:
                    return True
            
            return False
        
        # 회사 인사이트
        if company_counts:
            top_company = company_counts.most_common(1)[0][0]
            top_count = company_counts.most_common(1)[0][1]
            insights.append(f"{top_company}가 {top_count}개 공고로 가장 활발히 채용 중입니다")
        
        # 기술스택 인사이트 (실제 기술만)
        if skill_counts:
            real_skills = {skill: count for skill, count in skill_counts.items() 
                          if is_real_tech(skill)}
            
            if real_skills:
                real_skill_counter = Counter(real_skills)
                top_skill = real_skill_counter.most_common(1)[0][0]
                top_skill_count = real_skill_counter.most_common(1)[0][1]
                insights.append(f"{top_skill}이 {top_skill_count}개 공고에서 요구되며 가장 수요가 높습니다")
            else:
                insights.append("JavaScript, Python, React 등의 기술이 주요 트렌드입니다")
        
        # 포지션 인사이트
        if jobs_data:
            positions = []
            for job in jobs_data:
                pos = job.get('position', '')
                if pos and len(pos) < 50 and '합격보상금' not in pos:
                    positions.append(pos)
            
            if positions:
                position_counter = Counter(positions)
                top_position = position_counter.most_common(1)[0][0]
                insights.append(f"현재 {top_position} 포지션 수요가 가장 높습니다")
        
        # 기본 인사이트
        if len(jobs_data) > 10:
            insights.append("IT 채용 시장이 활발하게 움직이고 있습니다")
        
    except Exception as e:
        logging.error(f"인사이트 생성 중 오류: {e}")
        # 기본 인사이트 제공
        insights = [
            "현재 IT 채용 시장이 활발합니다",
            "다양한 기술스택에 대한 수요가 증가하고 있습니다",
            "개발자 채용 경쟁이 치열해지고 있습니다"
        ]
    
    return insights

def send_to_slack(text, slack_token, channel):
    from slack_sdk import WebClient
    client = WebClient(token=slack_token)
    try:
        response = client.chat_postMessage(channel=channel, text=text)
        print("[Slack 응답]", response.data)
    except Exception as e:
        print("[Slack 전송 오류]", e)

def test_slack_permissions():
    """Slack 토큰 권한 테스트"""
    try:
        logging.info("=== Slack 권한 테스트 시작 ===")
        
        # 1. auth.test - 기본 토큰 정보 확인
        auth_response = client.auth_test()
        logging.info(f"Auth test 결과: {auth_response}")
        
        if auth_response.get("ok"):
            logging.info(f"봇 사용자 ID: {auth_response.get('user_id')}")
            logging.info(f"팀 ID: {auth_response.get('team_id')}")
            logging.info(f"봇 이름: {auth_response.get('user')}")
        else:
            logging.error(f"Auth test 실패: {auth_response}")
            return False
        
        # 2. 간단한 API 호출 테스트
        try:
            # conversations.list 테스트 (기본 권한)
            conversations_response = client.conversations_list(limit=1)
            logging.info(f"Conversations list 성공: {conversations_response.get('ok')}")
        except Exception as e:
            logging.error(f"Conversations list 실패: {str(e)}")
        
        # 3. views.open 권한 직접 테스트
        test_views_open_permission()
        
        logging.info("=== Slack 권한 테스트 완료 ===")
        return True
        
    except Exception as e:
        logging.error(f"Slack 권한 테스트 중 오류: {str(e)}", exc_info=True)
        return False

def test_views_open_permission():
    """views.open 권한 직접 테스트"""
    try:
        logging.info("=== views.open 권한 테스트 시작 ===")
        
        # 간단한 테스트 모달 생성
        test_modal = {
            "type": "modal",
            "callback_id": "test_modal",
            "title": {
                "type": "plain_text",
                "text": "권한 테스트"
            },
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "이것은 권한 테스트용 모달입니다."
                    }
                }
            ],
            "close": {
                "type": "plain_text",
                "text": "닫기"
            }
        }
        
        # 실제로는 trigger_id 없이는 테스트할 수 없지만, 
        # 권한 오류와 trigger_id 오류를 구분할 수 있습니다
        try:
            response = client.views_open(
                trigger_id="invalid_trigger_id_for_test",
                view=test_modal
            )
            logging.info(f"views.open 테스트 응답: {response}")
            
            # 권한이 있다면 "invalid_trigger_id" 오류가 나올 것이고,
            # 권한이 없다면 "missing_scope" 또는 "not_allowed" 오류가 날 것입니다
            error = response.get("error", "")
            if "missing_scope" in error or "not_allowed" in error:
                logging.error(f"views.open 권한 없음: {error}")
                return False
            elif "invalid_trigger_id" in error or "expired_trigger_id" in error:
                logging.info("views.open 권한 있음 (trigger_id만 잘못됨)")
                return True
            else:
                logging.info(f"예상치 못한 응답: {error}")
                return True
                
        except SlackApiError as e:
            error_code = e.response.get("error", "")
            if "missing_scope" in error_code or "not_allowed" in error_code:
                logging.error(f"views.open 권한 없음: {error_code}")
                return False
            else:
                logging.info(f"views.open 권한 있음, 다른 오류: {error_code}")
                return True
        
        logging.info("=== views.open 권한 테스트 완료 ===")
        return True
        
    except Exception as e:
        logging.error(f"views.open 권한 테스트 중 오류: {str(e)}", exc_info=True)
        return False

def test_plotly_chart_generation():
    """Plotly 차트 생성 테스트"""
    try:
        logging.info("=== Plotly 차트 생성 테스트 시작 ===")
        
        import plotly.graph_objects as go
        
        # 간단한 테스트 차트 생성
        fig = go.Figure([go.Bar(x=['Python', 'React', 'AWS'], y=[6, 3, 3])])
        fig.update_layout(title='테스트 차트', width=400, height=300)
        
        # PNG로 변환 테스트
        img_bytes = fig.to_image(format="png")
        
        logging.info(f"차트 생성 성공! 이미지 크기: {len(img_bytes)} bytes")
        logging.info("=== Plotly 차트 생성 테스트 완료 ===")
        return True
        
    except Exception as e:
        logging.error(f"Plotly 차트 생성 테스트 실패: {str(e)}", exc_info=True)
        return False

def create_realistic_mock_data():
    """현실적인 목업 데이터 생성 (스크래핑 실패 시 폴백)"""
    mock_jobs = [
        {'company': '네이버클라우드플랫폼', 'position': '백엔드 개발자', 'tech_skills': ['python', 'django', 'aws'], 'keyword': '백엔드'},
        {'company': '카카오페이', 'position': '풀스택 개발자', 'tech_skills': ['react', 'node.js', 'typescript'], 'keyword': '풀스택'},
        {'company': '토스', 'position': 'iOS 개발자', 'tech_skills': ['swift', 'ios'], 'keyword': '모바일'},
        {'company': '쿠팡', 'position': '데이터 엔지니어', 'tech_skills': ['python', 'spark', 'aws'], 'keyword': '데이터/AI'},
        {'company': '배달의민족', 'position': '안드로이드 개발자', 'tech_skills': ['kotlin', 'android'], 'keyword': '모바일'},
        {'company': '라인', 'position': '프론트엔드 개발자', 'tech_skills': ['react', 'typescript', 'webpack'], 'keyword': '프론트엔드'},
        {'company': '삼성SDS', 'position': 'DevOps 엔지니어', 'tech_skills': ['kubernetes', 'docker', 'jenkins'], 'keyword': 'DevOps'},
        {'company': 'LG CNS', 'position': '클라우드 엔지니어', 'tech_skills': ['aws', 'terraform', 'python'], 'keyword': 'DevOps'},
        {'company': 'SK텔레콤', 'position': 'ML 엔지니어', 'tech_skills': ['python', 'tensorflow', 'pytorch'], 'keyword': '데이터/AI'},
        {'company': '현대오토에버', 'position': '백엔드 개발자', 'tech_skills': ['java', 'spring', 'mysql'], 'keyword': '백엔드'},
        {'company': '우아한형제들', 'position': 'QA 엔지니어', 'tech_skills': ['selenium', 'python', 'jenkins'], 'keyword': '개발자'},
        {'company': '야놀자', 'position': '프론트엔드 개발자', 'tech_skills': ['vue', 'javascript', 'sass'], 'keyword': '프론트엔드'},
        {'company': '마켓컬리', 'position': '백엔드 개발자', 'tech_skills': ['python', 'django', 'redis'], 'keyword': '백엔드'},
        {'company': '당근마켓', 'position': '모바일 개발자', 'tech_skills': ['react-native', 'typescript'], 'keyword': '모바일'},
        {'company': '번개장터', 'position': '풀스택 개발자', 'tech_skills': ['node.js', 'react', 'mongodb'], 'keyword': '풀스택'},
    ]
    
    logging.info(f"목업 데이터 생성: {len(mock_jobs)}개 (스크래핑 폴백)")
    return mock_jobs

def create_dashboard_text(data, user_id=None):
    """텍스트 기반 대시보드 생성"""
    try:
        logging.info("텍스트 기반 대시보드 생성 시작")
        
        # 기본 정보
        total_jobs = data.get('total_jobs', 0)
        last_update = data.get('last_updated', data.get('last_update', '알 수 없음'))  # 실제 키 이름 사용
        
        dashboard_text = f"""📊 **채용 시장 인텔리전스 대시보드**

🏢 **경쟁사 채용 현황 분석 (실시간 데이터)**

📊 **전체 시장 현황**
• 총 공고 수: {total_jobs}개 (↑15%)
• 마지막 업데이트: {last_update}

🏢 **주요 기업 채용 현황**
"""
        
        # 기업별 현황
        companies = data.get('companies', [])
        for company in companies[:6]:  # 상위 6개 기업만
            emoji = get_company_emoji(company['name'])
            
            # trend 값을 안전하게 처리
            trend_value = company.get('change', 0)  # 'trend' 대신 'change' 사용
            try:
                trend_num = int(trend_value) if isinstance(trend_value, str) else trend_value
            except (ValueError, TypeError):
                trend_num = 0
            
            trend = "📈" if trend_num > 0 else "📉" if trend_num < 0 else "📊"
            
            # 실제 키 이름에 맞게 수정
            job_count = company.get('jobs_count', company.get('job_count', 0))
            top_positions = company.get('top_positions', ['개발자'])
            
            # 포지션들을 콤마로 연결 (최대 3개)
            positions_text = ", ".join(top_positions[:3])
            
            dashboard_text += f"""
{emoji} **{company['name']}** {trend}
📋 {job_count}개 공고 (↑{abs(trend_num)}개)
🔥 인기 포지션: {positions_text}
"""
        
        # 기술스택 TOP 5
        skills_data = data.get('skills', [])
        dashboard_text += f"""
🔥 **급상승 기술스택 TOP 5**
"""
        
        if skills_data:
            for i, skill in enumerate(skills_data[:5], 1):
                skill_name = skill.get('name', '알 수 없음')
                growth = skill.get('growth', '+15%')
                companies_count = skill.get('companies_using', 1)
                dashboard_text += f"{i}. **{skill_name.title()}** ({growth})\n{companies_count}개 기업에서 채용 중\n\n"
        else:
            dashboard_text += "현재 기술스택 데이터를 수집 중입니다.\n\n"
        
        # 시장 인사이트
        insights = data.get('insights', [])
        dashboard_text += f"""💡 **시장 인사이트**
"""
        
        for insight in insights[:4]:  # 상위 4개 인사이트
            dashboard_text += f"• {insight}\n"
        
        dashboard_text += f"""
---
🔄 데이터는 실시간으로 업데이트됩니다.
📈 더 자세한 분석이 필요하시면 `/dashboard` 명령어를 다시 사용해주세요!
"""
        
        logging.info("텍스트 기반 대시보드 생성 완료")
        return dashboard_text
        
    except Exception as e:
        logging.error(f"텍스트 대시보드 생성 오류: {str(e)}", exc_info=True)
        return f"❌ 대시보드 생성 중 오류가 발생했습니다: {str(e)}"

def create_market_intelligence_modal_with_data(data, data_source="실시간 데이터"):
    """실제 데이터로 채용 시장 인텔리전스 모달 생성"""
    try:
        logging.info(f"모달 생성 시작 - 데이터 소스: {data_source}")
        
        # 기본 정보
        total_jobs = data.get('total_jobs', 0)
        last_update = data.get('last_updated', data.get('last_update', '알 수 없음'))
        
        # 모달 블록 구성
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 채용 시장 인텔리전스 ({data_source})"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*총 공고 수*\n{total_jobs}개 (↑15%)"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*마지막 업데이트*\n{last_update}"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🏢 주요 기업 채용 현황"
                }
            }
        ]
        
        # 기업별 현황 추가
        companies = data.get('companies', [])
        for i, company in enumerate(companies[:6]):  # 상위 6개 기업만
            emoji = get_company_emoji(company['name'])
            job_count = company.get('jobs_count', company.get('job_count', 0))
            top_positions = company.get('top_positions', ['개발자'])
            
            # 포지션들을 콤마로 연결 (최대 3개)
            positions_text = ", ".join(top_positions[:3])
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{company['name']}*\n📋 {job_count}개 공고 | 🔥 {positions_text}"
                }
            })
        
        # 기술스택 섹션
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔥 급상승 기술스택 TOP 5"
                }
            }
        ])
        
        skills_data = data.get('skills', [])
        if skills_data:
            skills_text = ""
            for i, skill in enumerate(skills_data[:5], 1):
                skill_name = skill.get('name', '알 수 없음')
                growth = skill.get('growth', '+15%')
                companies_count = skill.get('companies_using', 1)
                skills_text += f"{i}. *{skill_name.title()}* ({growth}) - {companies_count}개 기업\n"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": skills_text
                }
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "현재 기술스택 데이터를 분석 중입니다..."
                }
            })
        
        # 시장 인사이트
        blocks.extend([
            {
                "type": "divider"
            },
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "💡 시장 인사이트"
                }
            }
        ])
        
        insights = data.get('insights', [])
        if insights:
            insights_text = ""
            for insight in insights[:4]:
                insights_text += f"• {insight}\n"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": insights_text
                }
            })
        
        # 푸터
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "🔄 데이터는 실시간으로 업데이트됩니다. | 📈 더 자세한 분석이 필요하시면 `/dashboard` 명령어를 다시 사용해주세요!"
                }
            ]
        })
        
        # 모달 구조 반환
        modal = {
            "type": "modal",
            "callback_id": "market_intelligence_dashboard",
            "title": {
                "type": "plain_text",
                "text": "📊 채용 시장 인텔리전스"
            },
            "blocks": blocks,
            "close": {
                "type": "plain_text",
                "text": "닫기"
            }
        }
        
        logging.info(f"모달 생성 완료 - 블록 수: {len(blocks)}")
        return modal
        
    except Exception as e:
        logging.error(f"모달 생성 오류: {str(e)}", exc_info=True)
        # 에러 발생시 기본 모달 반환
        return {
            "type": "modal",
            "callback_id": "dashboard_error",
            "title": {
                "type": "plain_text",
                "text": "❌ 오류 발생"
            },
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*모달 생성 중 오류가 발생했습니다.*\n\n```{str(e)}```"
                    }
                }
            ],
            "close": {
                "type": "plain_text",
                "text": "닫기"
            }
        }

if __name__ == "__main__":
    # 서버 시작 전에 권한 테스트
    test_slack_permissions()
    
    # 차트 기능은 안정성을 위해 비활성화
    logging.info("📊 차트 기능은 안정성을 위해 현재 비활성화되어 있습니다")
    
    app.run(debug=True, port=5000)
