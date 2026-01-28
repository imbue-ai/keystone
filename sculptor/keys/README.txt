These keys are used for encoding and decoding Json Web Tokens in sculptor tests.

Generated like this (only 512 bits to keep the tokens reasonably short):

```
openssl genpkey -algorithm RSA -out private_test.pem -pkeyopt rsa_keygen_bits:512
openssl rsa -in private.pem -pubout -out public_test.pem
```
