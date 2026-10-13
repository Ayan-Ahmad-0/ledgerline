FROM python:3.11-slim

# Install system OCR and PDF binaries required by pytesseract and pdfplumber
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency declarations first for Docker build caching
COPY pyproject.toml README.md /app/

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy application source code
COPY . /app/

# Dynamic port binding for Render ($PORT)
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
