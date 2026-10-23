FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y \
    openjdk-11-jdk \
    maven \
    perl \
    git \
    wget \
    curl \
    python3 \
    python3-pip \
    subversion \
    unzip \
    cpanminus \
    sudo \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate UTF-8 locale (avoids javac encoding errors on old projects)
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Set Java 11
ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

# Install Defects4J
RUN git clone https://github.com/rjust/defects4j.git /defects4j
WORKDIR /defects4j
RUN cpanm --installdeps . && ./init.sh
ENV PATH="/defects4j/framework/bin:$PATH"

# Download PIT 1.6.7 jars into the workspace pitest-libs directory
RUN mkdir -p /workspaces/RepairLLaMA-prms-experiment/pitest-libs && \
    wget -q -P /workspaces/RepairLLaMA-prms-experiment/pitest-libs \
      https://repo1.maven.org/maven2/org/pitest/pitest/1.6.7/pitest-1.6.7.jar \
      https://repo1.maven.org/maven2/org/pitest/pitest-entry/1.6.7/pitest-entry-1.6.7.jar \
      https://repo1.maven.org/maven2/org/pitest/pitest-command-line/1.6.7/pitest-command-line-1.6.7.jar

# Install Python dependencies
RUN pip3 install pandas scipy scikit-learn matplotlib numpy

# Create a non-root "vscode" user
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    && chown -R $USERNAME:$USERNAME /defects4j \
    && chown -R $USERNAME:$USERNAME /workspaces

USER $USERNAME

WORKDIR /workspaces/RepairLLaMA-prms-experiment