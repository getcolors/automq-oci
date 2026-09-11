{ pkgs, ... }:
{
  languages.clojure.enable = true;
  languages.opentofu.enable = true;
  packages = with pkgs; [
    ansible oci-cli babashka curl jq openssh openssl kcat
    (python3.withPackages (ps: [ ps.boto3 ps.cryptography ]))
  ];
}
