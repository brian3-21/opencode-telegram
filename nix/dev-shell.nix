{
  pkgs,
}:

pkgs.mkShell {
  packages = [
    (pkgs.python3.withPackages (ps: [
      ps.python-telegram-bot
      ps.python-dotenv
    ]))
  ];
}
