# An s3 GRR hands pysam a presigned url, not `s3://`

`FsspecReadOnlyProtocol._get_file_url` returns `filesystem.sign(url)` for
the `s3` scheme, and every pysam open on an s3 GRR — tabix, VCF, BCF —
reads through that presigned `https://` url. GAIn does not hand htslib
`s3://bucket/key` and let its own s3 plugin authenticate, even though the
bundled htslib is built with `S3=yes` and can.

## Why this is out of scope

The proposal (gain#1371, filed from the review of gain#1339) was that the
presigned url is a bearer credential GAIn then has to redact out of every
message and keep off fd 2, and that it need not exist: htslib signs SigV4
itself from the same ambient credential chain botocore uses, so `s3://`
would leave nothing to redact and take the s3 opens out of the
unserialised verbosity bracket (gain#1360) entirely.

The mechanism was proved end to end before it was declined. Against a
MinIO behind TLS, in the `core` image, `pysam.TabixFile("s3://…")` with
`HTS_S3_HOST` and `HTS_S3_ADDRESS_STYLE=path` signs, connects and fetches
rows. It is refused because it moves authentication for one class of
read onto a second client that has to be configured, trusted and
debugged separately from the first, and the maintainer would rather own
one.

**Two clients, two chains.** botocore serves the manifest, contents and
metadata reads; htslib would serve the data reads. The bundled htslib
(1.23.1) resolves credentials from `AWS_*` env vars, `~/.aws/credentials`
profiles and `~/.s3cfg` only — no instance-metadata, no
web-identity/IRSA, no `role_arn`. Static keys are what s3 GRRs use today,
so this was judged acceptable on its own; it is still a constraint a
future deployment would discover as a 403 on data reads while every other
read of the same repository succeeds. The endpoint splits the same way:
`endpoint_url` / `S3_ENDPOINT_URL` for botocore, `HTS_S3_HOST` for
htslib, and GAIn would have to derive one from the other and keep them
agreeing.

**htslib's s3 reader does not honour `CURL_CA_BUNDLE`.** `hfile_libcurl.c`
reads it and sets `CURLOPT_CAINFO`; `hfile_s3.c`'s `s3_read_open` builds
its own curl handle and does not, so libcurl's compiled-in default
applies. In the PyPI pysam wheel that default is the manylinux path
`/etc/pki/tls/certs/ca-bundle.crt`, which Debian-family hosts — the
`core` and `web_api` images among them — do not have. Measured: the
presigned `https://` open verifies with `CURL_CA_BUNDLE` set; the `s3://`
open fails with `error setting certificate verify locations` regardless,
and passes only once a bundle is copied to that exact path. So shipping
`s3://` means either a CA symlink baked into every image and documented
for every wheel install, or waiting on an htslib fix. Neither is a cost
worth paying to avoid a redaction that already works.

**Bigwig keeps a presigned url in either world.** libBigWig speaks http
only, so `open_bigwig_file` would still hand out a signed url on the
uncached path (gain#1425), and the redaction and fd 2 machinery of ADR
0023 has to stay for it and for url-authed http GRRs. The proposal
shrinks that machinery's blast radius; it does not retire it.

**The functional argument has a cheaper answer.** The presigned url
expires (gain#1398: s3fs's default `expiration=100` seconds), and `s3://`
would remove that cap because htslib signs per request. An explicit long
`expiration` in `_get_file_url` bounds the cap at days instead of
minutes, which is enough for the handles GAIn keeps, and it is a
one-argument change.

## What is still in scope

**A longer signature.** gain#1398 stands: `_get_file_url` should pass an
explicit `expiration`, and ADR 0023 should record the lifetime it chose.

**Redaction of the presigned url.** ADR 0023's gain#1339 amendment is the
governing decision; the query-string redactor and the widened bracket
predicate stay.

**Revisiting if the ground moves.** Two things would reopen this: htslib's
`s3_read_open` honouring `CURL_CA_BUNDLE` (so the wheel verifies TLS
without a filesystem workaround), and htslib growing the
instance-metadata / web-identity credential sources, so the two chains
stop differing. With both in place the remaining objection is the second
endpoint knob, which GAIn could derive from `endpoint_url` itself.

## Prior requests

- iossifovlab/gain#1371 -- "Hand htslib s3:// directly instead of a
  presigned url, so the credential never enters the string"
