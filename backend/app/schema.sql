-- QueryPilot sample database (SQLite)
-- A small company: departments and employees on one side,
-- customers, products and orders on the other.
-- Dates are stored as ISO-8601 text ('YYYY-MM-DD'), which SQLite compares correctly as strings.

CREATE TABLE Departments (
    DepartmentID    INTEGER PRIMARY KEY,
    DepartmentName  TEXT    NOT NULL UNIQUE,
    Location        TEXT    NOT NULL,
    Budget          REAL    NOT NULL
);

CREATE TABLE Employees (
    EmployeeID      INTEGER PRIMARY KEY,
    FirstName       TEXT    NOT NULL,
    LastName        TEXT    NOT NULL,
    Email           TEXT    NOT NULL UNIQUE,
    JobTitle        TEXT    NOT NULL,
    DepartmentID    INTEGER NOT NULL REFERENCES Departments(DepartmentID),
    ManagerID       INTEGER REFERENCES Employees(EmployeeID),
    HireDate        TEXT    NOT NULL,
    Salary          REAL    NOT NULL
);

CREATE TABLE Customers (
    CustomerID      INTEGER PRIMARY KEY,
    FirstName       TEXT    NOT NULL,
    LastName        TEXT    NOT NULL,
    Email           TEXT    NOT NULL UNIQUE,
    City            TEXT    NOT NULL,
    State           TEXT    NOT NULL,
    Country         TEXT    NOT NULL DEFAULT 'USA',
    Segment         TEXT    NOT NULL,
    SignupDate      TEXT    NOT NULL
);

CREATE TABLE Products (
    ProductID       INTEGER PRIMARY KEY,
    ProductName     TEXT    NOT NULL UNIQUE,
    Category        TEXT    NOT NULL,
    UnitPrice       REAL    NOT NULL,
    StockQuantity   INTEGER NOT NULL,
    IsActive        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE Orders (
    OrderID         INTEGER PRIMARY KEY,
    CustomerID      INTEGER NOT NULL REFERENCES Customers(CustomerID),
    EmployeeID      INTEGER REFERENCES Employees(EmployeeID),
    OrderDate       TEXT    NOT NULL,
    Status          TEXT    NOT NULL,
    TotalAmount     REAL    NOT NULL
);

CREATE TABLE OrderItems (
    OrderItemID     INTEGER PRIMARY KEY,
    OrderID         INTEGER NOT NULL REFERENCES Orders(OrderID),
    ProductID       INTEGER NOT NULL REFERENCES Products(ProductID),
    Quantity        INTEGER NOT NULL,
    UnitPrice       REAL    NOT NULL,
    Discount        REAL    NOT NULL DEFAULT 0
);

-- Indexes on the most common join and filter paths.
-- Some frequently filtered columns (Customers.State, Employees.HireDate) are deliberately
-- left unindexed so the agent's index recommendations have something real to point at.
CREATE INDEX idx_employees_department ON Employees(DepartmentID);
CREATE INDEX idx_orders_customer      ON Orders(CustomerID);
CREATE INDEX idx_orders_date          ON Orders(OrderDate);
CREATE INDEX idx_orderitems_order     ON OrderItems(OrderID);
CREATE INDEX idx_orderitems_product   ON OrderItems(ProductID);
