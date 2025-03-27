FROM python:3.10

WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py /app/app.py

# Expose the port the app runs on
EXPOSE 8000

# Command to run uvicorn directly with explicit host and port
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]