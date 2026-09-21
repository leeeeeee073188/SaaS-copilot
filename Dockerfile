FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m -u 1000 saas-copilot
COPY --chown=saas-copilot:saas-copilot . .
RUN mkdir -p /app/data/flowforge && chown -R saas-copilot:saas-copilot /app/data
USER saas-copilot
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
