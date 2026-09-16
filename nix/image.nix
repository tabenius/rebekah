{ lib, dockerTools, bash, coreutils, curl, findutils, gnugrep, jq, procps, tini
, ollama ? null, opencode ? null }:

let
  entrypoint = ./entrypoint.sh;
  optionalServices =
    lib.optional (ollama != null) ollama
    ++ lib.optional (opencode != null) opencode;
in
dockerTools.buildLayeredImage {
  name = "rebekah";
  tag = "bootstrap";

  contents = [
    bash
    coreutils
    curl
    findutils
    gnugrep
    jq
    procps
    tini
    dockerTools.caCertificates
  ] ++ optionalServices;

  extraCommands = ''
    mkdir -p etc/rebekah var/lib/rebekah/{ollama,opencode,sylvae,weftmark,ephor} run/rebekah workspace

    printf '%s\n' \
      'root:x:0:0:root:/root:/bin/bash' \
      'rebekah:x:10000:10000:Rebekah supervisor:/var/lib/rebekah:/sbin/nologin' \
      'ollama:x:10001:10000:Ollama service:/var/lib/rebekah/ollama:/sbin/nologin' \
      'opencode:x:10002:10000:OpenCode service:/var/lib/rebekah/opencode:/sbin/nologin' \
      'sylvae:x:10003:10000:Sylvae service:/var/lib/rebekah/sylvae:/sbin/nologin' \
      'weftmark:x:10004:10000:WeftMark service:/var/lib/rebekah/weftmark:/sbin/nologin' \
      'ephor:x:10005:10000:Ephor connector:/var/lib/rebekah/ephor:/sbin/nologin' \
      > etc/passwd
    printf '%s\n' \
      'root:x:0:' \
      'rebekah:x:10000:rebekah,ollama,opencode,sylvae,weftmark,ephor' \
      > etc/group

    chown 10001:10000 var/lib/rebekah/ollama
    chown 10002:10000 var/lib/rebekah/opencode
    chown 10003:10000 var/lib/rebekah/sylvae
    chown 10004:10000 var/lib/rebekah/weftmark
    chown 10005:10000 var/lib/rebekah/ephor
    chmod 0750 var/lib/rebekah/*

    install -m 0555 ${entrypoint} usr/local/bin/rebekah-entrypoint
    ln -s rebekah-entrypoint usr/local/bin/rebekah-doctor
  '';

  config = {
    Entrypoint = [ "/bin/tini" "--" "/usr/local/bin/rebekah-entrypoint" ];
    Cmd = [ "serve" ];
    Env = [
      "HOME=/var/lib/rebekah"
      "PATH=/usr/local/bin:/bin"
      "OLLAMA_HOST=127.0.0.1:11434"
      "REBEKAH_STATE_DIR=/var/lib/rebekah"
      "REBEKAH_RUN_DIR=/run/rebekah"
    ];
    WorkingDir = "/workspace";
    Volumes = {
      "/var/lib/rebekah" = { };
      "/workspace" = { };
    };
    Labels = {
      "org.opencontainers.image.title" = "Rebekah";
      "org.opencontainers.image.description" = "Bootstrap runtime for governed agentic software work";
      "org.opencontainers.image.source" = "https://github.com/tabenius/rebekah";
    };
  };
}
