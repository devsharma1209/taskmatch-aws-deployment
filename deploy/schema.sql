CREATE DATABASE IF NOT EXISTS taskmatch;
USE taskmatch;

CREATE TABLE users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)     NOT NULL UNIQUE,
    password_hash VARCHAR(256)    NOT NULL,
    created_at    TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE jobs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(150)    NOT NULL,
    description TEXT            NOT NULL,
    budget      DECIMAL(10, 2)  NOT NULL,
    category    VARCHAR(50)     NOT NULL,
    posted_by   VARCHAR(100)    NOT NULL,
    user_id     INT,
    created_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE offers (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    job_id      INT             NOT NULL,
    user_id     INT             NOT NULL,
    amount      DECIMAL(10, 2)  NOT NULL,
    message     TEXT            NOT NULL,
    created_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Seed data so the app is never empty during demo
INSERT INTO jobs (title, description, budget, category, posted_by) VALUES
('Fix leaking kitchen tap', 'Need a plumber to fix a dripping tap in the kitchen. Standard mixer tap.', 80.00, 'Home Repairs', 'Alice'),
('Move a single bed frame', 'Need help moving a single bed frame from Surry Hills to Newtown. Ground floor both ends.', 50.00, 'Moving', 'Bob'),
('Assemble IKEA bookshelf', 'BILLY bookshelf, still in box. Tools provided.', 40.00, 'Assembly', 'Charlie');
