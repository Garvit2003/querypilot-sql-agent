-- Example schema for QueryPilot's "Use your own database" feature.
-- In the app: click the database chip (top right of the chat) > Change > upload this file.
-- Then try: "Which author has written the most books?", "Only Indian authors",
--           "Books published before 1960", "Average rating by genre".

CREATE TABLE Authors (
    AuthorID   INTEGER PRIMARY KEY,
    Name       TEXT NOT NULL,
    Country    TEXT NOT NULL
);

CREATE TABLE Books (
    BookID     INTEGER PRIMARY KEY,
    Title      TEXT NOT NULL,
    Genre      TEXT NOT NULL,
    AuthorID   INTEGER NOT NULL REFERENCES Authors(AuthorID),
    Published  TEXT NOT NULL            -- 'YYYY-MM-DD'
);

CREATE TABLE Reviews (
    ReviewID   INTEGER PRIMARY KEY,
    BookID     INTEGER NOT NULL REFERENCES Books(BookID),
    Rating     INTEGER NOT NULL CHECK (Rating BETWEEN 1 AND 5),
    ReviewDate TEXT NOT NULL
);

INSERT INTO Authors VALUES
 (1, 'Toni Morrison', 'USA'), (2, 'R. K. Narayan', 'India'), (3, 'Chimamanda Ngozi Adichie', 'Nigeria'),
 (4, 'Arundhati Roy', 'India'), (5, 'Haruki Murakami', 'Japan');

INSERT INTO Books VALUES
 (1, 'Beloved', 'Fiction', 1, '1987-09-01'), (2, 'Song of Solomon', 'Fiction', 1, '1977-08-12'),
 (3, 'Swami and Friends', 'Fiction', 2, '1935-01-01'), (4, 'The Guide', 'Fiction', 2, '1958-01-01'),
 (5, 'Americanah', 'Fiction', 3, '2013-05-14'), (6, 'We Should All Be Feminists', 'Essay', 3, '2014-07-29'),
 (7, 'The God of Small Things', 'Fiction', 4, '1997-04-04'), (8, 'Azadi', 'Essay', 4, '2020-09-01'),
 (9, 'Norwegian Wood', 'Fiction', 5, '1987-09-04'), (10, 'What I Talk About When I Talk About Running', 'Memoir', 5, '2007-10-12');

INSERT INTO Reviews VALUES
 (1, 1, 5, '2025-01-10'), (2, 1, 4, '2025-02-11'), (3, 3, 4, '2025-03-02'), (4, 4, 5, '2025-03-15'),
 (5, 5, 5, '2025-04-01'), (6, 5, 4, '2025-04-20'), (7, 7, 5, '2025-05-05'), (8, 8, 3, '2025-06-18'),
 (9, 9, 4, '2025-07-07'), (10, 10, 3, '2025-08-09'), (11, 6, 5, '2025-09-01'), (12, 2, 4, '2025-09-21');
