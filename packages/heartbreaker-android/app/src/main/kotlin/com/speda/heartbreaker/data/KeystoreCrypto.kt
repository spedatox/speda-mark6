package com.speda.heartbreaker.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * AES-256-GCM wrapping backed by the Android Keystore — the phone carries the key
 * to a very personal brain, so the uplink API key is never stored in plaintext.
 *
 * The symmetric key lives in the hardware-backed Keystore under [ALIAS] and never
 * leaves it; we only ever hand it plaintext to encrypt and ciphertext to decrypt.
 * Ciphertext is stored as Base64 of (12-byte IV ‖ GCM ciphertext+tag).
 *
 * Chosen over the deprecated androidx.security-crypto (EncryptedSharedPreferences)
 * so the port stays on a supported primitive.
 */
internal object KeystoreCrypto {

    private const val PROVIDER = "AndroidKeyStore"
    private const val ALIAS = "hb_uplink_key_v1"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val IV_LEN = 12
    private const val TAG_BITS = 128

    private fun secretKey(): SecretKey {
        val ks = KeyStore.getInstance(PROVIDER).apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, secretKey()) }
        val iv = cipher.iv // GCM generates a 12-byte IV
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(iv + ct, Base64.NO_WRAP)
    }

    /** Returns null if the blob is malformed or the key rotated out from under it. */
    fun decrypt(b64: String): String? = runCatching {
        val blob = Base64.decode(b64, Base64.NO_WRAP)
        val iv = blob.copyOfRange(0, IV_LEN)
        val ct = blob.copyOfRange(IV_LEN, blob.size)
        val cipher = Cipher.getInstance(TRANSFORM).apply {
            init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(TAG_BITS, iv))
        }
        cipher.doFinal(ct).toString(Charsets.UTF_8)
    }.getOrNull()
}
