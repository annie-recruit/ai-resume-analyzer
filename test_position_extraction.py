#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포지션 추출 테스트 스크립트 - 개선된 버전
원티드의 실제 DOM 구조를 분석
"""

import sys
import os
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_position_extraction():
    """포지션 추출 테스트 - 개선된 버전"""
    
    print("🔍 원티드 DOM 구조 분석 시작...")
    
    # Chrome 옵션 설정
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    driver = None
    try:
        # Chrome 드라이버 실행
        driver = webdriver.Chrome(options=chrome_options)
        print("✅ Chrome 드라이버 실행 성공")
        
        # 원티드 개발자 채용공고 페이지로 이동
        url = "https://www.wanted.co.kr/wdlist/518?country=kr&job_sort=job.latest_order&years=-1&locations=all"
        driver.get(url)
        print(f"📄 페이지 로드: {url}")
        
        # 페이지 로딩 대기
        time.sleep(5)
        
        # 1. 페이지 제목 확인
        print(f"🏷️ 페이지 제목: {driver.title}")
        
        # 2. 페이지 소스의 일부 확인 (채용공고 관련 키워드 찾기)
        page_source = driver.page_source
        if "채용" in page_source or "job" in page_source.lower() or "position" in page_source.lower():
            print("✅ 채용 관련 콘텐츠 발견")
        else:
            print("❌ 채용 관련 콘텐츠가 없음")
            
        # 3. 다양한 셀렉터로 요소 찾기
        selectors_to_try = [
            # 일반적인 채용공고 관련 셀렉터
            "[data-cy*='job']",
            "[data-testid*='job']", 
            "[data-position-id]",
            "[class*='JobCard']",
            "[class*='job-card']",
            "[class*='Job']",
            "[class*='position']",
            "[class*='Position']",
            
            # 링크 기반 셀렉터 
            "a[href*='/wd/']",
            "a[href*='position']",
            "a[href*='job']",
            
            # 구조 기반 셀렉터
            "article",
            "li[class*='item']",
            "div[class*='item']",
            "div[class*='card']",
            ".list-item",
            ".grid-item",
            
            # 텍스트 기반 (개발자 포함)
            "*:contains('개발자')",
            "*:contains('engineer')",
            "*:contains('developer')"
        ]
        
        for selector in selectors_to_try:
            try:
                if ":contains" in selector:
                    # XPath로 변환해서 텍스트 검색
                    if "개발자" in selector:
                        elements = driver.find_elements(By.XPATH, "//*[contains(text(), '개발자')]")
                    elif "engineer" in selector:
                        elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'engineer')]")
                    elif "developer" in selector:
                        elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'developer')]")
                    else:
                        continue
                else:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                
                if elements and len(elements) > 0:
                    print(f"🎯 '{selector}' - {len(elements)}개 요소 발견")
                    
                    # 처음 3개 요소의 텍스트 확인
                    for i, elem in enumerate(elements[:3]):
                        try:
                            text = elem.text.strip()
                            if text and len(text) > 0:
                                print(f"  {i+1}. {text[:100]}...")
                            else:
                                # 텍스트가 없으면 HTML 확인
                                html = elem.get_attribute('outerHTML')[:200]
                                print(f"  {i+1}. (텍스트 없음) HTML: {html}...")
                        except:
                            print(f"  {i+1}. (접근 불가)")
                    print()
            except Exception as e:
                continue
        
        # 4. 페이지의 모든 링크 중 채용공고 같은 것들 찾기
        print("🔗 페이지의 모든 채용공고 링크 분석:")
        all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
        job_links = []
        
        for link in all_links:
            try:
                href = link.get_attribute('href')
                text = link.text.strip()
                
                if href and ('/wd/' in href or 'position' in href or 'job' in href):
                    if text and len(text) > 3:
                        job_links.append((text, href))
            except:
                continue
        
        print(f"📋 발견된 채용공고 링크 수: {len(job_links)}")
        for i, (text, href) in enumerate(job_links[:10]):  # 처음 10개만
            print(f"  {i+1}. {text[:50]}... → {href[:50]}...")
        
        # 5. 가장 많은 텍스트를 가진 요소들 찾기 (채용공고일 가능성)
        print("\n📝 텍스트가 많은 요소들 (채용공고 카드일 가능성):")
        all_divs = driver.find_elements(By.CSS_SELECTOR, "div, article, li")
        text_rich_elements = []
        
        for div in all_divs:
            try:
                text = div.text.strip()
                if text and 50 <= len(text) <= 500:  # 적당한 길이의 텍스트
                    if any(keyword in text for keyword in ['개발자', '엔지니어', '채용', 'developer', 'engineer']):
                        text_rich_elements.append(text)
            except:
                continue
        
        print(f"🎯 채용 관련 텍스트 블록 수: {len(text_rich_elements)}")
        for i, text in enumerate(text_rich_elements[:5]):  # 처음 5개만
            print(f"  {i+1}. {text[:100]}...")
        
        print(f"\n{'='*60}")
        print("✅ DOM 구조 분석 완료")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {str(e)}")
        
    finally:
        if driver:
            driver.quit()
            print("🚪 브라우저 종료")

if __name__ == "__main__":
    test_position_extraction() 