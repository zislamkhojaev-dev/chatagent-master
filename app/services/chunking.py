"""Чанкинг: по предложениям (enhanced) и семантический по параграфам. ТЗ Б.2."""
import logging
import re
from typing import List

logger = logging.getLogger(__name__)

# Лимиты для семантического чанкинга (Б.2)
DEFAULT_CHUNK_MAX_CHARS = 800
DEFAULT_CHUNK_MIN_CHARS = 100
DEFAULT_OVERLAP_CHARS = 100


def smart_sentence_split(text: str) -> List[str]:
    """Разбиение по предложениям с учётом аббревиатур."""
    abbreviations = [
        "г", "ул", "д", "кв", "тел", "факс", "см", "км", "м", "кг",
        "руб", "коп", "т", "п", "с", "в", "н", "им", "др", "пр",
        "resp", "ya'ni", "va", "b", "yil", "kun", "soat",
        "tel", "faks", "email", "www", "http", "https", "uz",
    ]
    abbrev_pattern = "|".join(re.escape(abbr) for abbr in abbreviations)
    sentences = []
    current_sentence = ""
    parts = re.split(r"(\. )", text)
    for i in range(0, len(parts), 2):
        part = parts[i]
        separator = parts[i + 1] if i + 1 < len(parts) else ""
        current_sentence += part
        if separator == ". ":
            words = current_sentence.strip().split()
            if words:
                last_word = words[-1].lower().rstrip(".")
                if not re.match(
                    f"^({abbrev_pattern})$", last_word, re.IGNORECASE
                ) and not re.match(r"^\d+$", last_word):
                    sentences.append(current_sentence.strip() + ".")
                    current_sentence = ""
                    continue
            current_sentence += separator
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    final_sentences = []
    for sentence in sentences:
        sub_sentences = re.split(r"([.!?]) ", sentence)
        current_sub = ""
        for j in range(0, len(sub_sentences), 2):
            part = sub_sentences[j]
            punct = sub_sentences[j + 1] if j + 1 < len(sub_sentences) else ""
            current_sub += part
            if punct in [".", "!", "?"]:
                final_sentences.append(current_sub + punct)
                current_sub = ""
            elif punct:
                current_sub += punct + " "
        if current_sub.strip():
            final_sentences.append(current_sub.strip())
    return [s.strip() for s in final_sentences if s.strip()]


def semantic_chunking(
    text: str,
    chunk_max_size: int = DEFAULT_CHUNK_MAX_CHARS,
    chunk_min_size: int = DEFAULT_CHUNK_MIN_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> List[str]:
    """
    Семантический чанкинг по параграфам (ТЗ Б.2, вариант А).
    Разбиение по двойному переносу строки; слишком длинные параграфы — по предложениям.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return enhanced_text_chunking(text, chunk_max_size, overlap)
    chunks = []
    current = []
    current_len = 0
    for p in paragraphs:
        if current_len + len(p) + 2 <= chunk_max_size:
            current.append(p)
            current_len += len(p) + 2
        else:
            if current:
                chunk = "\n\n".join(current)
                if chunk:
                    chunks.append(chunk)
            if len(p) > chunk_max_size:
                for sent_chunk in enhanced_text_chunking(p, chunk_max_size, overlap):
                    chunks.append(sent_chunk)
                current = []
                current_len = 0
            else:
                current = [p]
                current_len = len(p) + 2
    if current:
        chunks.append("\n\n".join(current))
    if overlap > 0 and len(chunks) > 1:
        overlapped = []
        for i, c in enumerate(chunks):
            if i == 0:
                overlapped.append(c)
            else:
                prev = chunks[i - 1]
                # Обрезаем по границе слова: находим первый пробел после точки среза
                start = max(0, len(prev) - overlap)
                if start > 0:
                    space_idx = prev.find(" ", start)
                    start = space_idx + 1 if space_idx != -1 else start
                tail = prev[start:].strip()
                overlapped.append(tail + "\n\n" + c if tail else c)
        return overlapped
    return chunks


def enhanced_text_chunking(
    text: str, chunk_size: int = 800, overlap: int = 100
) -> List[str]:
    """Чанки по предложениям с фиксированным размером и overlap (fallback для длинных параграфов)."""
    sentences = smart_sentence_split(text)
    logger.info("Split into %s sentences", len(sentences))
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        sentence = (
            sentence.strip()
            + ("." if not sentence.strip().endswith((".", "!", "?")) else "")
            + " "
        )
        if len(current_chunk) + len(sentence) <= chunk_size:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
    if current_chunk:
        chunks.append(current_chunk.strip())
    if len(chunks) > 1 and overlap > 0:
        overlapped_chunks = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapped_chunks.append(chunk)
            else:
                prev_words = chunks[i - 1].split()[-overlap // 10 :]
                overlap_text = " ".join(prev_words) if prev_words else ""
                overlapped_chunks.append(overlap_text + " " + chunk)
        return overlapped_chunks
    return chunks
