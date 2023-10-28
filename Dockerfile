FROM python:3.12.0-slim-bullseye

RUN apt-get update && \
    apt-get -y dist-upgrade && \
    apt-get -y autoremove && \
    apt-get clean && \
    rm -r /var/lib/apt/lists/* && \
    groupadd -g 900 gnmi && \
    useradd -r -u 900 -g gnmi gnmi && \
    mkdir -p /usr/gnmi-exporter/src && chown gnmi:gnmi /usr/gnmi-exporter/src

WORKDIR /usr/gnmi-exporter/src

COPY --chown=gnmi:gnmi requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=gnmi:gnmi ./src .

USER 900
ENV PYTHONPATH="/usr/gnmi-exporter"
CMD ["python", "main.py", "--config", "/etc/gnmi-exporter/config.yaml"]