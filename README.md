# iptv

DISCLAIMER: The scripts and links provided on this GitHub page are for informational and educational purposes only. No files are hosted here; all links and information are publicly available on the web. We do not claim responsibility for any issues, damages, or losses that may arise from the use of these scripts or links. Users are advised to use them at their own risk and discretion.

No Warranty: The scripts and information are provided 'as is,' without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. We make no guarantee that the scripts will be error-free or suitable for any specific purpose.

External Links Disclaimer: This repository contains links to third-party websites or content. We do not control, endorse, or assume responsibility for the content, privacy policies, or practices of any third-party websites or services.

Always review and test any code or links before implementing them in your projects. You assume all risks associated with the implementation of this information.


## Automatic playlist refresh

GitHub Actions refreshes the nine sources every three hours. Each source is
independent: if LocalNow times out, for example, its previously published section
is retained while successful sources still replace their own sections. Existing
channel filtering, reviewed replacements, and duplicate precedence still apply.
Empty, malformed, or non-M3U responses are failures, not instructions to delete a
provider's channels. A first run without a saved section reports that provider as
unavailable. A run with no usable channels never replaces the published playlist.

Pluto playback renewal is also isolated. If renewal fails, the previous Pluto
section is retained and the other refreshed providers are still published.
Retained URLs can remain expired; retaining a channel does not mean it is working.

Each normal sync includes a **Provider refresh** table in the Actions run summary
and a token-free `provider-refresh-report` artifact. The table distinguishes
updated, retained, and unavailable sources; Pluto playback renewal has its own
result. Successful updates are committed **before** a final check marks an
incomplete refresh as failed. A red run can therefore have published useful
updates; inspect the summary for the provider requiring attention.

To exercise the source refresh locally without overwriting `main.m3u`:

```sh
python3 sync_buddylive_to_tv_channels.py --previous main.m3u \
  --m3u /tmp/iptv-preview.m3u --summary /tmp/iptv-summary.json --dry-run
python3 -m unittest discover -s tests -v
```

The normal workflow also passes `--renew-pluto`; run token renewal on the GitHub
publisher rather than committing playback URLs generated on a different network.
The three-channel Pluto pilot remains separate and requires its selected channels
to pass validation before publishing.
