ARG KDBX_BASE_IMAGE=portal.dl.kx.com/kxi-kdbx-base:1.19.3
FROM ${KDBX_BASE_IMAGE}

# The build context is assembled by build-images.sh. It contains an unpacked
# rt.qpk under rt/ and the shared simulator sources under q/.
COPY rt/ /opt/kx/app/rt/
COPY q/ /app/q/

USER root
RUN chmod -R a+rX /opt/kx/app/rt /app/q

USER 65534:65534

ENV QHOME=/opt/kx \
    RT_QPK_DIR=/opt/kx/app/rt \
    SIM_Q_DIR=/app/q

ENTRYPOINT ["/opt/kx/l64/q"]
