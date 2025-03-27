FROM python:3.10

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

RUN mkdir -p /app/data /app/temp_files
VOLUME ["/app/data"]

# Expose the port the app runs on
EXPOSE 8000

# Command to run uvicorn directly with explicit host and port
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]