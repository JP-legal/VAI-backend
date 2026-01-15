FROM python:3.12

WORKDIR /app

ENV PIP_TARGET=/app/packages
ENV PYTHONUSERBASE=/app/packages
ENV PYTHONPATH=/app/packages

COPY requirements.txt /app/requirements.txt

RUN PIP_TARGET="" pip install -r requirements.txt

COPY . ./app

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
