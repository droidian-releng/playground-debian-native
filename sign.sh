#!/bin/bash

echo DEBSIGN_KEYID=${GPG_STAGINGPRODUCTION_SIGNING_KEYID} > ~/.devscripts

cd /buildd
ls
echo "${GPG_STAGINGPRODUCTION_SIGNING_KEY}" | gpg --import
debsign *.changes
