"""Synthetic Austrian law-firm corpus generator.

Builds fictional but internally consistent case files (Akten) for a fictional
law firm (Kanzlei), plus gold questions with document- and span-level ground
truth, for evaluating the RecallHub/Quellex RAG agent.

Pipeline stages (each resumable, artifacts stored per run directory):

1. ``seeds``     fetch public court decisions from RIS as factual inspiration
2. ``plan``      orchestrator model builds a structured case ledger per case seed
3. ``write``     generator model writes every planned document from the ledger
4. ``render``    documents are rendered to DOCX/PDF/EML/scanned JPG/TXT files
5. ``questions`` gold questions with documents, quotes and expected tools
6. ``validate``  consistency checks across ledger, files and questions

Everything is configured through :class:`~.config.SynthConfig`, loaded from
JSON files and ``--set key=value`` overrides. Legal areas and case seeds are
JSON files, so new areas and cases can be added without code changes.
"""

__version__ = "0.1.0"
