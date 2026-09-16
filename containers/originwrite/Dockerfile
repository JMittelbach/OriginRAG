FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    latexmk texlive-latex-base texlive-latex-recommended texlive-latex-extra \
    texlive-fonts-recommended texlive-science && rm -rf /var/lib/apt/lists/*
ENV openin_any=p openout_any=p
WORKDIR /source
USER 65534:65534
