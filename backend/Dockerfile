FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
COPY requirements.txt ./
COPY schedule.csv ./
COPY fare.csv ./

ENV FLASK_APP=backend/app.py
EXPOSE 5000

CMD ["flask", "run", "--host=0.0.0.0"]
