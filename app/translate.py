"""Ewe <-> French translation with Meta NLLB-200 (No Language Left Behind)."""
import logging

from .config import settings

logger = logging.getLogger(__name__)

EWE = "ewe_Latn"
FRENCH = "fra_Latn"


class NLLBTranslator:
    def __init__(self, model_name: str = settings.nllb_model):
        self.model_name = model_name
        self.tokenizer = None
        self.model = None

    def load(self) -> None:
        if self.model is not None:
            return
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        logger.info("Loading %s", self.model_name)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
        self.model.eval()

    def translate(self, text: str, source: str, target: str) -> str:
        if not text.strip():
            return ""
        import torch

        self.load()
        self.tokenizer.src_lang = source
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            tokens = self.model.generate(
                **inputs,
                forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(target),
                max_new_tokens=400,
            )
        return self.tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]

    def ewe_to_french(self, text: str) -> str:
        return self.translate(text, EWE, FRENCH)

    def french_to_ewe(self, text: str) -> str:
        return self.translate(text, FRENCH, EWE)
