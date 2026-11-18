FROM python:3.10

WORKDIR /app

COPY requirements.txt .
COPY dataplexutils_metadata_wizard-0.0.2.tar.gz .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"] 
