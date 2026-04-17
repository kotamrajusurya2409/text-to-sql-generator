"""
RAG (Retrieval-Augmented Generation) Module
===========================================
Stores and retrieves similar SQL queries to provide context
for better SQL generation.

Features:
- Semantic search using sentence transformers
- Keyword matching fallback
- Robust error handling
"""

import numpy as np
from typing import List, Dict
import os

# Try to import sentence transformers
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    print("WARNING: sentence-transformers not installed")
    print("Install with: pip install sentence-transformers")


class SimpleRAG:
    """Simple RAG for SQL query matching with fallback support"""
    
    def __init__(self):
        self.embeddings = None
        self.queries = []
        self.model = None
        
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                print("Loading sentence transformer model...")
                self.model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2",
                    device='cpu'
                )
                print("SUCCESS: Sentence transformer model loaded")
            except Exception as e:
                print(f"WARNING: Failed to load sentence transformer: {e}")
                print("INFO: RAG will use keyword matching as fallback")
                self.model = None
        else:
            print("INFO: Using keyword matching for RAG")
    
    def add(self, question: str, sql: str = "", explanation: str = ""):
        """
        Add a query to the RAG database
        
        Args:
            question: Natural language question
            sql: SQL query
            explanation: Query explanation
        """
        # Handle dictionary input
        if isinstance(question, dict):
            self.queries.append(question)
        else:
            self.queries.append({
                "question": question,
                "sql": sql,
                "explanation": explanation
            })
        
        # Recompute embeddings if model is available
        if self.model is not None:
            try:
                questions = [q["question"] for q in self.queries]
                self.embeddings = self.model.encode(questions)
            except Exception as e:
                print(f"WARNING: Error computing embeddings: {e}")
                self.embeddings = None
    
    def add_query(self, question: str, sql: str, explanation: str = ""):
        """Alternative method name for compatibility"""
        self.add(question, sql, explanation)
    
    def search(self, question: str, top_k: int = 3) -> str:
        """
        Search for similar queries
        
        Args:
            question: Query to search for
            top_k: Number of results to return
            
        Returns:
            Context string with similar queries
        """
        if not self.queries:
            return ""
        
        # Use embeddings if available
        if self.model is not None and self.embeddings is not None:
            try:
                return self._search_with_embeddings(question, top_k)
            except Exception as e:
                print(f"WARNING: Embedding search failed: {e}")
        
        # Fallback to keyword matching
        return self._search_with_keywords(question, top_k)
    
    def _search_with_embeddings(self, question: str, top_k: int) -> str:
        """Search using semantic embeddings"""
        query_embedding = self.model.encode([question])[0]
        
        # Compute cosine similarity
        similarities = []
        for emb in self.embeddings:
            sim = np.dot(query_embedding, emb) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(emb)
            )
            similarities.append(sim)
        
        # Get top k indices
        top_indices = np.argsort(similarities)[-top_k:][::-1]
        
        # Build context
        context_parts = []
        for idx in top_indices:
            if similarities[idx] > 0.3:  # Similarity threshold
                q = self.queries[idx]
                context_parts.append(
                    f"Q: {q['question']}\nSQL: {q['sql']}\n"
                )
        
        return "\n".join(context_parts)
    
    def _search_with_keywords(self, question: str, top_k: int) -> str:
        """Fallback keyword-based search"""
        question_lower = question.lower()
        question_words = set(question_lower.split())
        
        # Score each query by keyword overlap
        scores = []
        for query in self.queries:
            query_words = set(query["question"].lower().split())
            overlap = len(question_words & query_words)
            scores.append(overlap)
        
        # Get top k indices
        top_indices = np.argsort(scores)[-top_k:][::-1]
        
        # Build context
        context_parts = []
        for idx in top_indices:
            if scores[idx] > 0:  # At least one word match
                q = self.queries[idx]
                context_parts.append(
                    f"Q: {q['question']}\nSQL: {q['sql']}\n"
                )
        
        return "\n".join(context_parts)
    
    def clear(self):
        """Clear all stored queries"""
        self.queries = []
        self.embeddings = None


# Initialize RAG with error handling
try:
    RAG = SimpleRAG()
    print("SUCCESS: RAG system initialized")
except Exception as e:
    print(f"ERROR: RAG initialization failed: {e}")
    
    # Create a minimal fallback RAG
    class MinimalRAG:
        """Minimal fallback RAG without embeddings"""
        def __init__(self):
            self.queries = []
        
        def add(self, question, sql="", explanation=""):
            if isinstance(question, dict):
                self.queries.append(question)
            else:
                self.queries.append({"question": question, "sql": sql, "explanation": explanation})
        
        def add_query(self, question, sql, explanation=""):
            self.add(question, sql, explanation)
        
        def search(self, question, top_k=3):
            return ""
        
        def clear(self):
            self.queries = []
    
    RAG = MinimalRAG()
    print("INFO: Using minimal RAG fallback")
