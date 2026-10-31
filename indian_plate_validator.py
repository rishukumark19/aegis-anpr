"""
Indian Registration Number Plate Validator & Character Disambiguation Engine.
Handles Indian RTO conventions, Bharat Series (BH), and resolves common OCR confusions.
"""

import re
from typing import Dict, Optional, Tuple

# All recognized Indian State and Union Territory RTO codes
VALID_STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN",
    "GA", "GJ", "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD",
    "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY",
    "RJ", "SK", "TN", "TR", "TS", "UK", "UA", "UP", "WB"
}

# Character disambiguation maps
LETTER_TO_DIGIT: Dict[str, str] = {
    "O": "0", "D": "0", "Q": "0",
    "I": "1", "L": "1", "T": "1",
    "Z": "2",
    "E": "3",
    "A": "4",
    "S": "5",
    "G": "6",
    "B": "8",
}

DIGIT_TO_LETTER: Dict[str, str] = {
    "0": "O",
    "1": "I",
    "2": "Z",
    "3": "E",
    "4": "A",
    "5": "S",
    "6": "G",
    "8": "B",
}

# Standard patterns
STANDARD_INDIAN_PLATE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{4})$")
BHARAT_SERIES_PLATE = re.compile(r"^(\d{2})BH(\d{4})([A-Z]{1,2})$")


def clean_raw_ocr_text(raw_text: str) -> str:
    """Removes spaces, hyphens, dots, and common IND watermark prefixes."""
    text = re.sub(r"[^A-Za-z0-9]", "", raw_text.upper())
    # Remove leading 'IND' if detected from HSRP blue strip
    if text.startswith("IND") and len(text) > 8:
        text = text[3:]
    return text


def correct_character_at(char: str, expected_type: str) -> str:
    """Corrects a single character based on whether a digit or letter is expected."""
    if expected_type == "letter":
        if char.isdigit():
            return DIGIT_TO_LETTER.get(char, char)
        return char
    elif expected_type == "digit":
        if char.isalpha():
            return LETTER_TO_DIGIT.get(char, char)
        return char
    return char


def extract_candidate_plate(cleaned: str) -> str:
    """If text is too long (e.g. repeated OCR lines or burned-in video annotations), extracts the best plate slice."""
    if 8 <= len(cleaned) <= 11:
        return cleaned

    # Check for exact half repetition (e.g. DL2CQ3150DL2CQ3150)
    if len(cleaned) >= 16 and len(cleaned) % 2 == 0:
        half = len(cleaned) // 2
        if cleaned[:half] == cleaned[half:]:
            return cleaned[:half]

    # Look for Indian state code prefix
    for i in range(len(cleaned) - 7):
        sub2 = cleaned[i:i+2]
        sub2_corrected = "".join(DIGIT_TO_LETTER.get(c, c) for c in sub2)
        if sub2_corrected in VALID_STATE_CODES:
            for length in (10, 9, 8):
                if i + length <= len(cleaned):
                    candidate = cleaned[i:i+length]
                    # Verify last 4 chars have digits
                    digits_at_end = sum(1 for c in candidate[-4:] if c.isdigit() or c in LETTER_TO_DIGIT)
                    if digits_at_end >= 3:
                        return candidate

    return cleaned


def validate_and_format_indian_plate(raw_text: str) -> Tuple[str, bool, Optional[str]]:
    """
    Validates and applies positional heuristic correction to an Indian plate string.
    Returns:
        (formatted_plate, is_valid, detected_state_or_category)
    """
    cleaned = clean_raw_ocr_text(raw_text)
    cleaned = extract_candidate_plate(cleaned)
    if len(cleaned) < 8 or len(cleaned) > 11:
        return cleaned, False, None

    # Check for Bharat Series: YY BH #### XX (e.g. 22BH1234AA)
    if "BH" in cleaned[2:5]:
        bh_chars = list(cleaned)
        # Year: positions 0-1 (digits)
        bh_chars[0] = correct_character_at(bh_chars[0], "digit")
        bh_chars[1] = correct_character_at(bh_chars[1], "digit")
        # 'BH'
        bh_chars[2] = "B"
        bh_chars[3] = "H"
        # 4 digits
        for i in range(4, min(8, len(bh_chars))):
            bh_chars[i] = correct_character_at(bh_chars[i], "digit")
        # Ending letters
        for i in range(8, len(bh_chars)):
            bh_chars[i] = correct_character_at(bh_chars[i], "letter")
        candidate = "".join(bh_chars)
        match = BHARAT_SERIES_PLATE.match(candidate)
        if match:
            return candidate, True, "Bharat Series (BH)"

    # Standard State Plate: State(2 letters) + RTO(1-2 digits) + Series(1-3 letters) + Number(4 digits)
    chars = list(cleaned)
    n = len(chars)

    # State code: first 2 characters must be letters
    chars[0] = correct_character_at(chars[0], "letter")
    chars[1] = correct_character_at(chars[1], "letter")
    state_code = "".join(chars[0:2])

    # Last 4 characters must be digits
    for i in range(n - 4, n):
        chars[i] = correct_character_at(chars[i], "digit")

    # Middle characters: RTO code (digits) followed by Series (letters)
    # Positions 2 & 3 are typically RTO digits (e.g. 01, 03, 12)
    chars[2] = correct_character_at(chars[2], "digit")
    if n >= 10:
        chars[3] = correct_character_at(chars[3], "digit")
        # Positions 4 up to n-4 are Series letters
        for i in range(4, n - 4):
            chars[i] = correct_character_at(chars[i], "letter")
    elif n == 9:
        # Could be single RTO digit (e.g. DL 1 C 1234) or 2 RTO digits + 1 letter (e.g. DL 01 A 1234)
        # Check if char[3] is closer to digit or letter
        if chars[3].isdigit() or chars[3] in LETTER_TO_DIGIT:
            chars[3] = correct_character_at(chars[3], "digit")
            chars[4] = correct_character_at(chars[4], "letter")
        else:
            chars[3] = correct_character_at(chars[3], "letter")

    candidate = "".join(chars)
    match = STANDARD_INDIAN_PLATE.match(candidate)
    if match and state_code in VALID_STATE_CODES:
        return candidate, True, state_code
    elif match:
        # Matches format but state code may be regional or temporary
        return candidate, True, state_code

    return candidate, False, None

