FROM python:3.12-slim
RUN pip install --no-cache-dir "edge-tts>=7.0.0"
COPY app.py /app.py
EXPOSE 8080
CMD ["python", "/app.py"]
