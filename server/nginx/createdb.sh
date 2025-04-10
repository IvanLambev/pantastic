#!/bin/bash

echo "⏳ Waiting for Cassandra to be ready..."
until cqlsh cassandra-db -e "DESCRIBE KEYSPACES"; do
  sleep 5
done

echo "✅ Cassandra is up. Creating keyspace and tables..."

cqlsh cassandra-db -e "
CREATE KEYSPACE IF NOT EXISTS pantastic
WITH REPLICATION = { 'class': 'SimpleStrategy', 'replication_factor': 1 };

USE pantastic;

CREATE TABLE IF NOT EXISTS customers (
    customer_id UUID PRIMARY KEY,
    email TEXT,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    city TEXT,
    total_orders INT,
    total_spent DECIMAL,
    created_at TIMESTAMP,
    password TEXT,
    admin INT,
    worker INT
);

CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id UUID PRIMARY KEY,
    name TEXT,
    longitude DECIMAL,
    latitude DECIMAL,
    address TEXT,
    opening_hours MAP<TEXT, TEXT>,
    delivery_people MAP<UUID, TEXT>,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delivery_people (
    delivery_person_id UUID PRIMARY KEY,
    name TEXT,
    phone TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    item_id UUID PRIMARY KEY,
    restaurant_id UUID,
    name TEXT,
    description TEXT,
    price DECIMAL,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    customer_id UUID,
    order_id UUID PRIMARY KEY,
    restaurant_id UUID,
    products MAP<UUID, INT>,
    total_price DECIMAL,
    discount DECIMAL,
    payment_method TEXT,
    delivery_method TEXT,
    address TEXT,
    status TEXT,
    created_at TIMESTAMP,
    delivery_person UUID,
    delivery_fee DECIMAL,
    delivery_time TIMESTAMP,
    estimated_delivery_time TIMESTAMP,
    delivery_person_name TEXT,
    delivery_person_phone TEXT
);

CREATE TABLE IF NOT EXISTS discounts (
    discount_id UUID PRIMARY KEY,
    created_by UUID,
    discount_code TEXT,
    created_at TIMESTAMP,
    discount_percentage INT,
    expires_at TIMESTAMP
);

"

echo "✅ Tables and keyspace created!"
