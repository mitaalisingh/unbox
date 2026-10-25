FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/model_cache \
    HF_HOME=/app/hf_cache \
    BEHIND_PROXY=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download both small models at build time so the live site never has to fetch them
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
 && python -c "from huggingface_hub import hf_hub_download; hf_hub_download('onnx-community/siglip2-base-patch16-224-ONNX', 'onnx/vision_model_bnb4.onnx')"
COPY webapp.py sbclient.py images.py search.py naming.py ./
COPY naming_data naming_data
COPY templates templates
COPY static static
EXPOSE 10000
CMD gunicorn "webapp:create_app()" --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120
