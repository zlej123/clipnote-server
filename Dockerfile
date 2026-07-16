FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
RUN git clone --depth 1 https://github.com/zlej123/clipnote /opt/clipnote
ENV CLIPNOTE_PATH=/opt/clipnote

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV HOST=0.0.0.0 PORT=8787
EXPOSE 8787
CMD ["python", "app.py"]
