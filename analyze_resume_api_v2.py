import os
from flask import Flask, request, jsonify
import docx2txt
import tempfile
import requests
import openai
import json
import re
import io
import plotly.graph_objects as go
from datetime import datetime

# Slack Bot Token을 환경 변수에서 가져오기
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "your-slack-bot-token-here")

app = Flask(__name__)

@app.route("/analyze_resume", methods=["POST"])
def analyze_resume():
    try:
        # API 키 가져오기
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not api_key:
            print("API 키가 없습니다.")
            return jsonify({"error": "OpenAI API 키가 필요합니다."}), 401
        
        print(f"API 키 확인됨: {api_key[:10]}...")
        openai.api_key = api_key
        
        # 요청 데이터 확인
        data = request.get_json()
        if not data:
            print("요청 데이터가 없습니다.")
            return jsonify({"error": "요청 데이터가 없습니다."}), 400
            
        resume_text = data.get("resume_text", "")
        if not resume_text:
            print("이력서 텍스트가 비어있습니다.")
            return jsonify({"error": "이력서 텍스트가 비어있습니다."}), 400
        
        print(f"이력서 텍스트 수신 (길이: {len(resume_text)})")
        print(f"이력서 내용 샘플: {resume_text[:100]}...")
        
        # GPT 분석 프롬프트
        prompt = f"""
        다음 이력서를 분석하여 아래 형식으로 결과를 JSON 형태로 출력해주세요.
        다른 설명은 하지 말고 JSON 형식으로만 출력해주세요:

        1. 이름 추출 (없으면 '미기재'로 표시)
        2. 총 경력 연차 계산
        3. 가장 두드러지는 강점 3가지 추출
        4. 이 사람을 한 문장으로 표현하는 캐치프레이즈 작성
        5. 역량을 아래 3가지로 분류하여 설명:
           - 도메인 지식: IT/기술/비즈니스 분야의 전문 지식
           - 기술 역량: 실무에서 사용하는 도구/플랫폼/기술
           - 소프트 스킬: 리더십/커뮤니케이션/문제해결 능력

        이력서 내용:
        {resume_text}

        다음 JSON 형식으로만 출력:
        {{
            "name": "이름 (없으면 '미기재'로 표시)",
            "total_years": 숫자만_입력,
            "top_strengths": [
                "핵심 강점 1",
                "핵심 강점 2",
                "핵심 강점 3"
            ],
            "catchphrase": "지원자를 한 문장으로 표현한 캐치프레이즈",
            "skill_cards": {{
                "domain_knowledge": "도메인 지식 상세 설명",
                "tech_skills": "보유한 기술 역량 상세 설명",
                "soft_skills": "소프트 스킬 상세 설명"
            }}
        }}
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a resume analyzer that only outputs in valid JSON format."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5
        )
        
        result = response.choices[0].message.content.strip()
        
        # JSON 형식 검증
        try:
            parsed_result = json.loads(result)
            
            # 채널 ID 가져오기
            channel_id = request.form.get("channel_id") or request.json.get("channel_id")
            
            # 분석 결과 블록 생성 (channel_id 전달)
            blocks = create_stat_card_blocks(parsed_result, channel_id)
            
            return jsonify({"blocks": blocks})
        except json.JSONDecodeError as e:
            return jsonify({"error": f"Invalid JSON format: {str(e)}"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def parse_skills(tech_skills_text):
    """기술 스킬 텍스트에서 스킬과 숙련도를 추출"""
    skills_dict = {}
    if not tech_skills_text:
        return skills_dict
        
    # 정규식 패턴으로 "기술: 숙련도%" 형식 추출
    pattern = r'([^:,]+):\s*(\d+)%'
    matches = re.findall(pattern, tech_skills_text)
    
    for skill, value in matches:
        skills_dict[skill.strip()] = int(value)
    
    return skills_dict

def create_plotly_radar_chart(skills_dict):
    """Plotly를 사용한 레이더 차트 생성"""
    if not skills_dict:
        return None
        
    categories = list(skills_dict.keys())
    values = list(skills_dict.values())
    
    # 첫번째 값을 마지막에 추가하여 폐곡선 생성
    categories.append(categories[0])
    values.append(values[0])
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatterpolar(
        r=values,
        theta=categories,
        fill='toself',
        name='Skills',
        line=dict(color='rgb(31, 119, 180)'),
        fillcolor='rgba(31, 119, 180, 0.5)'
    ))
    
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100],
                showline=True,
                linewidth=1,
                gridcolor='lightgray',
                gridwidth=0.5
            ),
            angularaxis=dict(
                showline=True,
                linewidth=1,
                gridcolor='lightgray',
                gridwidth=0.5
            ),
            bgcolor='white'
        ),
        showlegend=False,
        title=dict(
            text="Technical Skills Radar Chart",
            x=0.5,
            y=0.95,
            font=dict(size=20)
        ),
        paper_bgcolor='white',
        plot_bgcolor='white',
        width=800,
        height=800,
        margin=dict(l=80, r=80, t=100, b=80)
    )
    
    # 이미지로 저장
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png', engine='kaleido')
    img_bytes.seek(0)
    return img_bytes

def upload_image_to_slack(image_bytes, title, channel_id):
    """이미지를 Slack에 업로드하고 URL 반환"""
    try:
        response = requests.post(
            'https://slack.com/api/files.upload',
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            files={
                'file': ('chart.png', image_bytes, 'image/png')
            },
            data={
                "channels": channel_id,
                "title": title,
                "initial_comment": "📊 기술 스킬 레이더 차트"
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                return result['file']['url_private']
        return None
    except Exception as e:
        print(f"이미지 업로드 오류: {str(e)}")
        return None

def create_stat_card_blocks(result, channel_id=None):
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
        }
    ])
    
    # 채널 ID가 있는 경우에만 추가 액션 섹션 포함
    if channel_id:
        blocks.extend([
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

if __name__ == "__main__":
    print("분석 API 서버 시작 (포트 5050)")
    app.run(port=5050, debug=True)