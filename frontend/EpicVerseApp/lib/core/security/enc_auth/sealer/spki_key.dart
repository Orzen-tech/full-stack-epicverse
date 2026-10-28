import 'dart:convert';
import 'dart:typed_data';

import 'package:pointycastle/asn1.dart';
import 'package:pointycastle/export.dart';

import 'errors.dart';
import 'validation.dart';

const String _rsaEncryptionOid = '1.2.840.113549.1.1.1';

/// Parses a DER SubjectPublicKeyInfo holding an RSA public key (the format the server's key id is computed from).
/// Anything else, trailing bytes, or a modulus shorter than 3072 bits is refused.
RSAPublicKey parseRsaPublicKeySpki(Uint8List der) {
  final BigInt modulus;
  final BigInt exponent;
  try {
    final parser = ASN1Parser(der);
    final top = parser.nextObject() as ASN1Sequence;
    if (parser.hasNext()) throw const FormatException('trailing bytes');
    final algorithm = top.elements![0] as ASN1Sequence;
    final oid = algorithm.elements![0] as ASN1ObjectIdentifier;
    if (oid.objectIdentifierAsString != _rsaEncryptionOid || top.elements!.length != 2) {
      throw const FormatException('not an RSA key');
    }
    final bits = top.elements![1] as ASN1BitString;
    if (bits.unusedbits != 0) throw const FormatException('unused bits');
    final inner = ASN1Parser(Uint8List.fromList(bits.stringValues!));
    final keySequence = inner.nextObject() as ASN1Sequence;
    if (inner.hasNext()) throw const FormatException('trailing bytes');
    modulus = (keySequence.elements![0] as ASN1Integer).integer!;
    exponent = (keySequence.elements![1] as ASN1Integer).integer!;
  } on Object {
    throw const EnvelopeError(malformed); // never echo parser details
  }
  if (modulus.bitLength < rsaMinBits || exponent < BigInt.two || modulus.sign <= 0) {
    throw const EnvelopeError(malformed);
  }
  // The parser does not check declared lengths against the data (a truncated key still parses). Re-encode what was parsed and
  // require it to equal the input byte for byte: this refuses truncation, trailing bytes and any non-canonical encoding.
  final Uint8List canonical;
  try {
    canonical = _encodeSpki(modulus, exponent);
  } on Object {
    throw const EnvelopeError(malformed);
  }
  if (canonical.length != der.length) throw const EnvelopeError(malformed);
  for (var i = 0; i < der.length; i++) {
    if (canonical[i] != der[i]) throw const EnvelopeError(malformed);
  }
  return RSAPublicKey(modulus, exponent);
}

Uint8List _encodeSpki(BigInt modulus, BigInt exponent) {
  final rsaKey = ASN1Sequence()
    ..add(ASN1Integer(modulus))
    ..add(ASN1Integer(exponent));
  final algorithm = ASN1Sequence()
    ..add(ASN1ObjectIdentifier.fromIdentifierString(_rsaEncryptionOid))
    ..add(ASN1Null());
  final bits = ASN1BitString(stringValues: rsaKey.encode())..unusedbits = 0;
  return (ASN1Sequence()
        ..add(algorithm)
        ..add(bits))
      .encode();
}

/// 'k' + the first 12 base64url characters of SHA-256(DER); identical to testkeys.kid_for in the Python reference.
String keyIdForSpki(Uint8List der) {
  final digest = SHA256Digest().process(der);
  return 'k${base64Url.encode(digest).substring(0, 12)}';
}
