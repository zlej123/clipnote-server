FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
# 코어를 커밋으로 고정한다 (외부 리뷰 #8) — 예전에는 빌드 시점의 main을 설치해서,
# 같은 서버 커밋도 날짜에 따라 다른 코어와 조합됐다(재현 불가). 갱신은 CORE_REF 파일을
# 새 커밋으로 바꿔서 한다 (호환 검증 후).
COPY CORE_REF .
RUN pip install --no-cache-dir "git+https://github.com/zlej123/stepkeeper@$(cat CORE_REF)"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py bridge_reports.py .

ENV HOST=0.0.0.0 PORT=8787
EXPOSE 8787
CMD ["python", "app.py"]
