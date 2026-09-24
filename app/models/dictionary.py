from sqlalchemy import Column, Integer, String, Text
from app.db.session import Base

class PhraseDictionary(Base):
    __tablename__ = "phrase_dictionary"
    
    chinese_phrase = Column(String(255), primary_key=True, index=True)
    vietnamese_phrase = Column(Text, nullable=False)

class NamesDictionary(Base):
    __tablename__ = "names_dictionary"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, nullable=True, index=True)
    chinese_name = Column(String(255), nullable=False, index=True)
    vietnamese_name = Column(String(255), nullable=False)

class UnblockDictionary(Base):
    __tablename__ = "unblock_dictionary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chinese_word = Column(String(255), nullable=False, index=True)
    vietnamese_word = Column(String(255), nullable=False)
