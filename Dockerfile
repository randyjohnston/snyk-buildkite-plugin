FROM python:3.7.13-alpine3.15

ENV PATH=$PATH:/root/.local/bin:/usr/local/sbt/bin

RUN apk update && apk add --update npm jq go libc-dev openjdk8
RUN apk add --no-cache nss bash ncurses git
RUN npm config set unsafe-perm true
RUN npm install -g snyk

CMD python3 plugin/snyk.py
