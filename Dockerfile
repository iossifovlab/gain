FROM condaforge/mambaforge:latest


ADD environment.yml /
ADD dev-environment.yml /

RUN /opt/conda/bin/mamba update -n base -c conda-forge conda
RUN /opt/conda/bin/mamba env create --name gain --file /environment.yml
RUN /opt/conda/bin/mamba env update --name gain --file /dev-environment.yml


ENV PATH /opt/conda/envs/gain/bin:$PATH

RUN mkdir -p /data && mkdir -p /code

WORKDIR /code

SHELL ["/bin/bash", "-c"]
