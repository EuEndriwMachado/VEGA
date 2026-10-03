CREATE DATABASE IF NOT EXISTS vega
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE vega;

CREATE TABLE IF NOT EXISTS memories (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    memory_type ENUM('permanent', 'selective', 'episodic', 'semantic', 'preference', 'procedural', 'error') NOT NULL,
    memory_key VARCHAR(191) NOT NULL,
    value_text TEXT NOT NULL,
    category VARCHAR(100) NOT NULL DEFAULT 'geral',
    importance DECIMAL(5,4) NOT NULL DEFAULT 0.5000,
    confidence DECIMAL(5,4) NOT NULL DEFAULT 0.5000,
    source VARCHAR(100) NOT NULL DEFAULT 'migration',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_memory (memory_type, memory_key),
    KEY idx_memory_category (memory_type, category),
    FULLTEXT KEY ft_memory_text (memory_key, value_text)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id CHAR(36) NOT NULL,
    role ENUM('user', 'assistant', 'system') NOT NULL,
    content MEDIUMTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_conversation_session (session_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS daily_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_date DATE NOT NULL,
    role VARCHAR(30) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_daily_date (event_date, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS schema_metadata (
    `key` VARCHAR(100) NOT NULL,
    value_text VARCHAR(255) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`key`)
) ENGINE=InnoDB;

INSERT INTO schema_metadata (`key`, value_text)
VALUES ('schema_version', '1')
ON DUPLICATE KEY UPDATE value_text = VALUES(value_text);
