{ dockerTools, bash, coreutils, curl, findutils, git, gnugrep, jq, ollama
, ephor, opencode, procps, python3, tini, util-linux, weftmark, sylvae }:

let
  # The gateway runs on its own Python with PyJWT + cryptography for OIDC JWT
  # verification. The token path needs only the stdlib (PyJWT is imported lazily
  # in gateway.py), but bundling both keeps the external (OIDC) path available.
  gatewayPython = python3.withPackages (ps: [ ps.pyjwt ps.cryptography ]);
in
dockerTools.buildLayeredImage {
  name = "rebekah";
  tag = "latest";

  contents = [
    bash coreutils curl ephor findutils git gnugrep jq ollama opencode procps sylvae
    tini util-linux weftmark dockerTools.caCertificates
  ];

  extraCommands = ''
    mkdir -p \
      etc/rebekah \
      usr/local/bin \
      usr/local/lib/rebekah \
      run/rebekah \
      var/lib/rebekah/ollama \
      var/lib/rebekah/opencode \
      var/lib/rebekah/sylvae/runs \
      var/lib/rebekah/sylvae/skills \
      var/lib/rebekah/weftmark \
      var/lib/rebekah/ephor \
      var/lib/rebekah/gateway \
      workspace

    # Each service gets its own primary group (gid == uid) so a 0750 state
    # directory grants group access only to the owning service. A single shared
    # group (with every service as a member) would let any service read every
    # other service's state directory, defeating the per-UID isolation.
    printf '%s\n' \
      'root:x:0:0:root:/root:/bin/bash' \
      'rebekah:x:10000:10000:Rebekah supervisor:/var/lib/rebekah:/sbin/nologin' \
      'ollama:x:10001:10001:Ollama service:/var/lib/rebekah/ollama:/sbin/nologin' \
      'opencode:x:10002:10002:OpenCode service:/var/lib/rebekah/opencode:/sbin/nologin' \
      'sylvae:x:10003:10003:Sylvae service:/var/lib/rebekah/sylvae:/sbin/nologin' \
      'weftmark:x:10004:10004:WeftMark service:/var/lib/rebekah/weftmark:/sbin/nologin' \
      'gateway:x:10005:10005:Rebekah API gateway:/var/lib/rebekah:/sbin/nologin' \
      'ephor:x:10006:10006:Ephor governance service:/var/lib/rebekah/ephor:/sbin/nologin' \
      > etc/passwd
    printf '%s\n' \
      'root:x:0:' \
      'rebekah:x:10000:' \
      'ollama:x:10001:' \
      'opencode:x:10002:' \
      'sylvae:x:10003:' \
      'weftmark:x:10004:' \
      'gateway:x:10005:' \
      'ephor:x:10006:' \
      > etc/group

    chmod 0750 var/lib/rebekah/*

    install -m 0555 ${./entrypoint.sh} usr/local/bin/rebekah-entrypoint
    install -m 0555 ${./ephor-connector.sh} usr/local/bin/rebekah-ephor
    install -m 0555 ${./govern.sh} usr/local/bin/rebekah-govern
    install -m 0555 ${./sylvae-evidence.sh} usr/local/bin/rebekah-sylvae-evidence
    ln -s rebekah-entrypoint usr/local/bin/rebekah-doctor
    ln -s rebekah-entrypoint usr/local/bin/rebekah-health

    # The authenticated API gateway and its launcher (pins the gateway Python
    # that carries PyJWT + cryptography).
    install -m 0555 ${./gateway.py} usr/local/lib/rebekah/gateway.py
    printf '%s\n' \
      '#!${bash}/bin/bash' \
      'exec ${gatewayPython}/bin/python3 /usr/local/lib/rebekah/gateway.py "$@"' \
      > usr/local/bin/rebekah-gateway
    chmod 0555 usr/local/bin/rebekah-gateway

    # The gateway's built-in web console (static, served same-origin).
    mkdir -p usr/local/share/rebekah
    cp -r ${./ui} usr/local/share/rebekah/ui
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
      "GIT_CONFIG_COUNT=1"
      "GIT_CONFIG_KEY_0=safe.directory"
      "GIT_CONFIG_VALUE_0=/workspace"
      "OLLAMA_HOST=127.0.0.1:11434"
      "REBEKAH_OLLAMA_MODEL=qwen2.5:0.5b"
      "SYLVAE_OLLAMA_MODEL=qwen2.5:0.5b"
      "OLLAMA_API_BASE=http://127.0.0.1:11434"
      "OPENCODE_HOST=127.0.0.1"
      "OPENCODE_PORT=4096"
      "SYLVAE_HOST=127.0.0.1"
      "SYLVAE_PORT=8971"
      "WEFTMARK_HOST=127.0.0.1"
      "WEFTMARK_PORT=8765"
      "EPHOR_URL=http://127.0.0.1:9800"
      "EPHOR_HOST=127.0.0.1"
      "EPHOR_PORT=9800"
      # API gateway: loopback by default; set REBEKAH_GATEWAY_HOST + TLS to
      # expose it on the LAN. Exposes only WeftMark until told otherwise.
      "REBEKAH_GATEWAY_ENABLE=1"
      "REBEKAH_GATEWAY_HOST=127.0.0.1"
      "REBEKAH_GATEWAY_PORT=8080"
      "REBEKAH_GATEWAY_EXPOSE=weftmark opencode ollama"
      "REBEKAH_GATEWAY_UI=1"
      "REBEKAH_GATEWAY_UI_DIR=/usr/local/share/rebekah/ui"
      # Default browser login: SQLite username/password. On first boot the
      # gateway seeds an admin user; provide REBEKAH_ADMIN_PASSWORD or a random
      # one is generated and logged once. DB lives on the persistent state dir.
      "REBEKAH_AUTH_PASSWORD=1"
      "REBEKAH_AUTH_DB=/var/lib/rebekah/gateway/auth.db"
      "REBEKAH_ADMIN_USER=admin"
    ];
    ExposedPorts = { "8080/tcp" = { }; };
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
