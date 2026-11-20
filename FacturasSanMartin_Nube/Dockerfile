FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV NUBE=1 DATOS=/data
VOLUME /data
CMD ["python", "servidor.py"]
