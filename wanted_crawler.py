import requests
import pandas as pd
from datetime import datetime
import os

def crawl_wanted_jobs():
    jobs = []
    offset = 0
    limit = 20
    while True:
        url = f"https://www.wanted.co.kr/api/v4/jobs?country=kr&tag_type_ids=518&job_sort=job.latest_order&limit={limit}&offset={offset}"
        res = requests.get(url)
        data = res.json()
        job_list = data.get("data", [])
        if not job_list:
            break
        for job in job_list:
            jobs.append({
                "company": job["company"]["name"],
                "title": job["position"],
                "region": job["address"]["location"] if job["address"] else "",
                "url": f"https://www.wanted.co.kr/wd/{job['id']}",
                "date": datetime.now().strftime("%Y-%m-%d")
            })
        offset += limit
        if offset >= data.get("total", 0):
            break
    return pd.DataFrame(jobs)

def analyze_top5_jobs(csv_path="wanted_jobs.csv"):
    df = pd.read_csv(csv_path)
    top_jobs = df['title'].value_counts().head(5)
    print("[직무별 TOP 5]")
    print(top_jobs)
    return top_jobs

def compare_companies(csv_path="wanted_jobs.csv"):
    df = pd.read_csv(csv_path)
    # 기술스택, 복지 컬럼이 없을 수 있으니 예시로 처리
    if 'tech_stack' not in df.columns or 'benefits' not in df.columns:
        print("[경고] 기술스택/복지 컬럼이 없습니다. 예시 데이터로 대체합니다.")
        df['tech_stack'] = ["Python, Django"]*len(df)
        df['benefits'] = ["점심제공, 원격근무"]*len(df)
    print("\n[경쟁사별 상세 포지션 비교 (기술스택, 복지)]")
    for company, group in df.groupby('company'):
        print(f"\n회사: {company}")
        for idx, row in group.iterrows():
            print(f"  - 포지션: {row['title']}")
            print(f"    기술스택: {row['tech_stack']}")
            print(f"    복지: {row['benefits']}")

def generate_gpt_insight(top_jobs):
    try:
        import openai
    except ImportError:
        print("[경고] openai 패키지가 설치되어 있지 않습니다. GPT 인사이트 생략.")
        return
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[경고] OPENAI_API_KEY 환경변수 없음. GPT 인사이트 생략.")
        return
    client = openai.OpenAI(api_key=api_key)
    prompt = f"이번 달 채용 데이터에서 가장 많이 뽑는 직무 TOP 5는 다음과 같습니다: {top_jobs.to_dict()}. 이 데이터를 바탕으로 한 줄 요약 인사이트를 한국어로 생성해 주세요."
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "너는 채용 데이터 분석가야. 반드시 한글로 한 줄 요약만 해줘."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    insight = response.choices[0].message.content.strip()
    print("\n[GPT 자동 인사이트]")
    print(insight)
    return insight

def send_to_slack(text, slack_token, channel):
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json"
    }
    data = {
        "channel": channel,
        "text": text
    }
    response = requests.post(url, headers=headers, json=data)
    print("[Slack 응답]", response.json())

def main():
    top_jobs = analyze_top5_jobs()
    compare_companies()
    # GPT 인사이트 생성
    try:
        import openai
        api_key = os.getenv("OPENAI_API_KEY")
        client = openai.OpenAI(api_key=api_key)
        prompt = f"이번 달 채용 데이터에서 가장 많이 뽑는 직무 TOP 5는 다음과 같습니다: {top_jobs.to_dict()}. 이 데이터를 바탕으로 한 줄 요약 인사이트를 한국어로 생성해 주세요."
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "너는 채용 데이터 분석가야. 반드시 한글로 한 줄 요약만 해줘."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        insight = response.choices[0].message.content.strip()
        print("\n[GPT 자동 인사이트]")
        print(insight)
        # 슬랙 전송
        SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "your-slack-bot-token-here")  # 환경변수에서 가져오기
        SLACK_CHANNEL = "U08TYB64MD3"             # ← 여기에 채널명(예: #general) 또는 채널ID(예: C12345678) 입력!
        send_to_slack(insight, SLACK_TOKEN, SLACK_CHANNEL)
    except Exception as e:
        print("[GPT/Slack 오류]", e)

if __name__ == "__main__":
    main()