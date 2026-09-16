{ dockerTools, bash, coreutils, curl, findutils, git, gnugrep, jq, ollama
, opencode, procps, tini, util-linux, weftmark, sylvae }:

dockerTools.buildLayeredImage {
  name = "rebekah";
  tag = "latest";

  contents = [
    bash coreutils curl findutils git gnugrep jq ollama opencode procps sylvae
    tini util-linux weftmark dockerTools.caCertificates
  ];

  extraCommands = ''
    mkdir -p \
      etc/rebekah \
      usr/local/bin \
      run/rebekah \
      var/lib/rebekah/ollama \
      var/lib/rebekah/opencode \
      var/lib/rebekah/sylvae/runs \
      var/lib/rebekah/sylvae/skills \
      var/lib/rebekah/weftmark \
      workspace

    printf '%s\n' \
      'root:x:0:0:root:/root:/bin/bash' \
      'rebekah:x:10000:10000:Rebekah supervisor:/var/lib/rebekah:/sbin/nologin' \
      'ollama:x:10001:10000:Ollama service:/var/lib/rebekah/ollama:/sbin/nologin' \
      'opencode:x:10002:10000:OpenCode service:/var/lib/rebekah/opencode:/sbin/nologin' \
      'sylvae:x:10003:10000:Sylvae service:/var/lib/rebekah/sylvae:/sbin/nologin' \
      'weftmark:x:10004:10000:WeftMark service:/var/lib/rebekah/weftmark:/sbin/nologin' \
      > etc/passwd
    printf '%s\n' \
      'root:x:0:' \
      'rebekah:x:10000:rebekah,ollama,opencode,sylvae,weftmark' \
      > etc/group

    chmod 0750 var/lib/rebekah/*

    install -m 0555 ${./entrypoint.sh} usr/local/bin/rebekah-entrypoint
    ln -s rebekah-entrypoint usr/local/bin/rebekah-doctor
    ln -s rebekah-entrypoint usr/local/bin/rebekah-health
  '';

  config = {
    Entrypoint = [ "/bin/tini" "--" "/usr/local/bin/rebekah-entrypoint" ];
    Cmd = [ "serve" ];
    Env = [
      "HOME=/var/lib/rebekah"
      "PATH=/usr/local/bin:/bin"
      "REBEKAH_STATE_DIR=/var/lib/rebekah"
      "REBEKAH_RUN_DIR=/run/rebekah"
      "REBEKAH_WORKSPACE=/workspace"
      "OLLAMA_HOST=127.0.0.1:11434"
      "OPENCODE_HOST=127.0.0.1"
      "OPENCODE_PORT=4096"
      "SYLVAE_HOST=127.0.0.1"
      "SYLVAE_PORT=8971"
      "WEFTMARK_HOST=127.0.0.1"
      "WEFTMARK_PORT=8765"
    ];
    WorkingDir = "/workspace";
    Volumes = { "/var/lib/rebekah" = { }; "/workspace" = { }; };
    Healthcheck = {
      Test = [ "CMD" "/usr/local/bin/rebekah-health" ];
      Interval = 10000000000;
      Timeout = 3000000000;
      Retries = 6;
      StartPeriod = 30000000000;
    };
    Labels = {
      "org.opencontainers.image.title" = "Rebekah";
      "org.opencontainers.image.description" = "Runtime for governed agentic software work";
      "org.opencontainers.image.source" = "https://github.com/tabenius/rebekah";
    };
  };
}
