FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

EXPOSE 7681

ENTRYPOINT ["/app/entrypoint.sh"]
