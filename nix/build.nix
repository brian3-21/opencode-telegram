{
  lib,
  python3,
  makeWrapper,
  src,
}:

python3.pkgs.buildPythonApplication {
  pname = "opencode-telegram";
  version = "0.1.0";
  pyproject = true;

  inherit src;

  nativeBuildInputs = [
    python3.pkgs.setuptools
    makeWrapper
  ];

  propagatedBuildInputs = [
    python3.pkgs.python-telegram-bot
    python3.pkgs.python-dotenv
  ];

  postInstall = ''
    wrapProgram "$out/bin/opencode-telegram" \
      --run 'mkdir -p "$HOME/.config/opencode-telegram"' \
      --run 'export OPENBOT_DIR="$HOME/.config/opencode-telegram"'
  '';

  meta = with lib; {
    description = "Telegram bot connected to opencode";
    license = licenses.mit;
    mainProgram = "opencode-telegram";
  };
}
