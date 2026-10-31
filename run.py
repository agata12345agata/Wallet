import hashlib

def extract_wallet_info(wallet_hash):
    """
    Function to extract the masterkey, salt, and iterations from a Bitcoin Core wallet.dat hash.

    Parameters:
    - wallet_hash: str
        The wallet.dat hash from which to extract the information.

    Returns:
    - tuple:
        A tuple containing the extracted masterkey, salt, and iterations.

    Raises:
    - ValueError:
        Raises an error if the wallet_hash is not a valid hash or if the required information cannot be extracted.
    """

    # Checking if the wallet_hash is a valid hash
    if not isinstance(wallet_hash, str) or len(wallet_hash) != 64:
        raise ValueError("Invalid wallet hash. Please provide a valid 64-character hash.")

    # Extracting the masterkey, salt, and iterations from the wallet_hash
    try:
        masterkey = wallet_hash[:32]
        salt = wallet_hash[32:48]
        iterations = int(wallet_hash[48:], 16)
    except:
        raise ValueError("Unable to extract masterkey, salt, and iterations from the wallet hash.")

    return masterkey, salt, iterations

# Example usage:
wallet_hash = "$bitcoin$64$24b4a225bbf86972233aec387401b536ef1fe333fd18cf15d13e8fe1b1acfbe2$16$7363b105beaf4028$36762$2$00$2$00"
masterkey, salt, iterations = extract_wallet_info(wallet_hash)
print(f"Masterkey: {masterkey}")
print(f"Salt: {salt}")
print(f"Iterations: {iterations}")