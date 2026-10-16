FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/model_cache \
    BEHIND_PROXY=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download the search model at build time so the live site never has to fetch it
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
COPY app.py search.py bundlekey.py ./
COPY templates templates
COPY static static
COPY deploy_data deploy_data
EXPOSE 10000
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120
