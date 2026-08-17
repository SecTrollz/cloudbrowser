FROM python:3.12-slim

WORKDIR /app

RUN pip install flask docker requests pyyaml vncdotool websockets --break-system-packages 2>/dev/null || \
    pip install flask docker requests pyyaml vncdotool websockets

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
