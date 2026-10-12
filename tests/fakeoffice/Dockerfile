FROM python:3.12-alpine
# a self-signed certificate for its HTTPS listener (8443): the "check the certificate" setting
RUN apk add --no-cache openssl && openssl req -x509 -newkey rsa:2048 -nodes -keyout /key.pem -out /cert.pem -days 3650 -subj /CN=fakeoffice
COPY server.py /server.py
CMD ["python", "-u", "/server.py"]
