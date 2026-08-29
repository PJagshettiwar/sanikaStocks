FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN adduser --disabled-password --no-create-home appuser
COPY . .
RUN chmod 600 *.session 2>/dev/null || true
USER appuser
CMD ["python", "main.py"]
