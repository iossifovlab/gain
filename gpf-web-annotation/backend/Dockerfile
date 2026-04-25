ARG COMPOSE_PROJECT_NAME=gpfwa
FROM ${COMPOSE_PROJECT_NAME}-gpf-image:latest AS build

RUN mamba install -y -n base -c conda-forge conda-pack=0.9.1

WORKDIR /app

COPY environment.yml .
RUN mamba env update -y -n gpf --file ./environment.yml

# Copy the rest of the application source code into the container
COPY . .

RUN mamba run -n gpf pip install .

RUN conda-pack -n gpf -o /tmp/gpf.tar && \
  mkdir /gpf && cd /gpf && tar xf /tmp/gpf.tar && \
  rm /tmp/gpf.tar

RUN /gpf/bin/conda-unpack


FROM ${COMPOSE_PROJECT_NAME}-ubuntu-image:latest AS runtime

COPY --from=build /gpf /gpf

RUN mkdir -p /logs


COPY ./scripts/supervisord.conf /etc/
COPY ./scripts/supervisord-bootstrap.sh /
COPY ./scripts/wait-for-it.sh /
RUN chmod +x /*.sh

# When image is run, run the code with the environment
# activated:
RUN echo "source /gpf/bin/activate" >> /etc/bash.bashrc
# Explicitly add gpf/bin to the path to aid certain tools 
# (like dask)in finding the needed executables
ENV PATH=/gpf/bin:$PATH

EXPOSE 80 443

ADD ./scripts/grr-definition.yaml /
ADD ./scripts/grr-definition-dir.yaml /

ENTRYPOINT ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
