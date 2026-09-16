import asyncio

from sqlalchemy import text

from database import engine


MIGRATIONS = (
    ("inventory_batches", "last_mile_cost", "DECIMAL(12,4) NOT NULL DEFAULT 0"),
    ("inventory_batches", "shipping_date", "DATETIME NULL"),
    ("inventory_batches", "user_id", "INT NULL"),
    ("inventory_batches", "dingtalk_order_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "total_amount", "DECIMAL(12,4) NULL"),
    ("inventory_batches", "shipping_cost_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "last_mile_cost_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "other_cost_no", "VARCHAR(64) NULL"),
    ("users", "store_id", "INT NULL"),
    ("sales", "user_id", "INT NULL"),
    ("sales", "store_id", "INT NULL"),
    ("products", "image", "VARCHAR(500) NULL"),
    ("products", "product_type", "VARCHAR(16) NOT NULL DEFAULT 'stable'"),
    ("products", "safe_stock_quantity", "INT NOT NULL DEFAULT 0"),
    ("settlement_entries", "store_id", "INT NULL"),
    ("settlement_entries", "mapping_status", "VARCHAR(32) NOT NULL DEFAULT 'confirmed'"),
    ("settlement_entries", "mapping_error", "VARCHAR(500) NULL"),
    ("settlement_entries", "import_batch_id", "INT NULL"),
    ("settlement_entries", "exchange_rate_to_cny", "DECIMAL(18,8) NULL"),
    ("settlement_entries", "exchange_rate_date", "DATE NULL"),
    ("settlement_entries", "exchange_rate_source", "VARCHAR(64) NULL"),
)

INDEX_MIGRATIONS = (
    ("inventory_batches", "ix_inventory_batches_arrived_at_id", "arrived_at, id"),
    (
        "inventory_batches",
        "ix_inventory_batches_product_arrived_at",
        "product_id, arrived_at",
    ),
    ("sales", "ix_sales_sold_at", "sold_at"),
    ("sales", "ix_sales_user_sold_at", "user_id, sold_at"),
    ("users", "ix_users_store_id", "store_id"),
    ("sales", "ix_sales_store_id", "store_id"),
    ("settlement_entries", "ix_settlement_entries_store_id", "store_id"),
    ("settlement_entries", "ix_settlement_entries_mapping_status", "mapping_status"),
    ("settlement_entries", "ix_settlement_entries_import_batch_id", "import_batch_id"),
)


async def _column_exists(connection, table_name: str, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.scalar_one_or_none() is not None


async def _index_exists(connection, table_name: str, index_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return result.scalar_one_or_none() is not None


async def migrate() -> None:
    """Add known backwards-compatible columns before serving requests."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sales_import_batches (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NULL,
                    store_id INT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    file_type VARCHAR(32) NOT NULL,
                    sheet_name VARCHAR(64) NULL,
                    total_rows INT NOT NULL DEFAULT 0,
                    settlement_rows INT NOT NULL DEFAULT 0,
                    sales_created INT NOT NULL DEFAULT 0,
                    skipped_duplicates INT NOT NULL DEFAULT 0,
                    pending_confirmation INT NOT NULL DEFAULT 0,
                    status VARCHAR(24) NOT NULL DEFAULT 'completed',
                    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    rolled_back_at DATETIME NULL,
                    rolled_back_by_user_id INT NULL,
                    INDEX ix_sales_import_batches_user_id (user_id),
                    INDEX ix_sales_import_batches_store_id (store_id),
                    INDEX ix_sales_import_batches_status (status),
                    INDEX ix_sales_import_batches_imported_at (imported_at),
                    INDEX ix_sales_import_batches_user_imported_at (user_id, imported_at)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS inventory_period_snapshots (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    accounting_period_id INT NOT NULL,
                    batch_id INT NOT NULL,
                    product_id INT NOT NULL,
                    store_id INT NULL,
                    quantity INT NOT NULL DEFAULT 0,
                    unit_cost DECIMAL(12,4) NOT NULL DEFAULT 0,
                    inventory_amount DECIMAL(16,4) NOT NULL DEFAULT 0,
                    impairment_rate DECIMAL(8,6) NOT NULL DEFAULT 0,
                    impairment_amount DECIMAL(16,4) NOT NULL DEFAULT 0,
                    book_value DECIMAL(16,4) NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_inventory_period_snapshots_period_batch
                        UNIQUE (accounting_period_id, batch_id),
                    INDEX ix_inventory_period_snapshots_accounting_period_id
                        (accounting_period_id),
                    INDEX ix_inventory_period_snapshots_batch_id (batch_id),
                    INDEX ix_inventory_period_snapshots_product_id (product_id),
                    INDEX ix_inventory_period_snapshots_store_id (store_id),
                    INDEX ix_inventory_period_snapshots_period_product
                        (accounting_period_id, product_id),
                    CONSTRAINT fk_inventory_period_snapshots_period
                        FOREIGN KEY (accounting_period_id) REFERENCES accounting_periods(id),
                    CONSTRAINT fk_inventory_period_snapshots_batch
                        FOREIGN KEY (batch_id) REFERENCES inventory_batches(id),
                    CONSTRAINT fk_inventory_period_snapshots_product
                        FOREIGN KEY (product_id) REFERENCES products(id),
                    CONSTRAINT fk_inventory_period_snapshots_store
                        FOREIGN KEY (store_id) REFERENCES stores(id)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS store_profit_loss_reports (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    store_id INT NOT NULL,
                    report_month DATE NOT NULL,
                    manual_values JSON NOT NULL,
                    remarks JSON NOT NULL,
                    metadata JSON NOT NULL,
                    updated_by_user_id INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT uq_store_profit_loss_reports_store_month
                        UNIQUE (store_id, report_month),
                    INDEX ix_store_profit_loss_reports_store_id (store_id),
                    INDEX ix_store_profit_loss_reports_month (report_month),
                    INDEX ix_store_profit_loss_reports_updated_by_user_id (updated_by_user_id),
                    CONSTRAINT fk_store_profit_loss_reports_store
                        FOREIGN KEY (store_id) REFERENCES stores(id),
                    CONSTRAINT fk_store_profit_loss_reports_user
                        FOREIGN KEY (updated_by_user_id) REFERENCES users(id)
                )
                """
            )
        )

        for table_name, column_name, definition in MIGRATIONS:
            if await _column_exists(connection, table_name, column_name):
                continue
            await connection.execute(
                text(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}")
            )
            print(f"Added {table_name}.{column_name}")

        for table_name, index_name, columns in INDEX_MIGRATIONS:
            if await _index_exists(connection, table_name, index_name):
                continue
            await connection.execute(
                text(f"CREATE INDEX `{index_name}` ON `{table_name}` ({columns})")
            )
            print(f"Added index {table_name}.{index_name}")

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(16) DEFAULT 'operator',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS platform_sku_mappings (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    platform VARCHAR(32) NOT NULL DEFAULT 'tiktok_shop',
                    platform_sku_id VARCHAR(128) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT uq_platform_sku_mappings_platform_sku
                        UNIQUE (platform, platform_sku_id),
                    INDEX ix_platform_sku_mappings_platform_active (platform, is_active)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS platform_sku_components (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    platform_sku_mapping_id INT NOT NULL,
                    product_id INT NOT NULL,
                    quantity_per_sale INT NOT NULL DEFAULT 1,
                    CONSTRAINT uq_platform_sku_components_mapping_product
                        UNIQUE (platform_sku_mapping_id, product_id),
                    INDEX ix_platform_sku_components_mapping_id (platform_sku_mapping_id),
                    INDEX ix_platform_sku_components_product_id (product_id),
                    CONSTRAINT fk_platform_sku_components_mapping
                        FOREIGN KEY (platform_sku_mapping_id) REFERENCES platform_sku_mappings(id),
                    CONSTRAINT fk_platform_sku_components_product
                        FOREIGN KEY (product_id) REFERENCES products(id)
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS settlement_entry_allocations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    settlement_entry_id INT NOT NULL,
                    product_id INT NOT NULL,
                    sale_id INT NULL,
                    component_name VARCHAR(500) NOT NULL,
                    multiplier INT NOT NULL DEFAULT 1,
                    quantity INT NOT NULL DEFAULT 0,
                    CONSTRAINT uq_settlement_entry_allocations_entry_product
                        UNIQUE (settlement_entry_id, product_id),
                    INDEX ix_settlement_entry_allocations_settlement_entry
                        (settlement_entry_id),
                    INDEX ix_settlement_entry_allocations_product (product_id),
                    INDEX ix_settlement_entry_allocations_sale (sale_id),
                    CONSTRAINT fk_settlement_entry_allocations_entry
                        FOREIGN KEY (settlement_entry_id) REFERENCES settlement_entries(id),
                    CONSTRAINT fk_settlement_entry_allocations_product
                        FOREIGN KEY (product_id) REFERENCES products(id),
                    CONSTRAINT fk_settlement_entry_allocations_sale
                        FOREIGN KEY (sale_id) REFERENCES sales(id)
                )
                """
            )
        )

        # Bundle rows link to sales through allocations, so NULL values on the
        # settlement row itself do not mean that product matching failed.
        await connection.execute(
            text(
                """
                UPDATE settlement_entries se
                SET se.mapping_status = 'confirmed',
                    se.mapping_error = NULL
                WHERE se.mapping_error IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM settlement_entry_allocations allocation
                    WHERE allocation.settlement_entry_id = se.id
                      AND allocation.sale_id IS NOT NULL
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM settlement_entry_allocations allocation
                    WHERE allocation.settlement_entry_id = se.id
                      AND allocation.sale_id IS NULL
                )
                """
            )
        )

        await connection.execute(
            text(
                """
                UPDATE settlement_entries se
                SET se.mapping_status = 'pending_confirmation'
                WHERE se.product_id IS NULL
                  AND se.sale_id IS NULL
                  AND se.mapping_status = 'confirmed'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM settlement_entry_allocations allocation
                    WHERE allocation.settlement_entry_id = se.id
                      AND allocation.sale_id IS NOT NULL
                  )
                """
            )
        )

        await connection.execute(
            text(
                """
                INSERT INTO sales_import_batches (
                    user_id, store_id, file_name, file_type, sheet_name,
                    total_rows, settlement_rows, sales_created,
                    skipped_duplicates, pending_confirmation, status, imported_at
                )
                SELECT
                    MAX(s.user_id),
                    MAX(se.store_id),
                    COALESCE(se.source_file, '历史导入'),
                    CASE
                        WHEN LOWER(COALESCE(se.source_file, '')) LIKE '%.csv' THEN 'legacy_csv'
                        ELSE 'order_detail'
                    END,
                    MAX(se.source_sheet),
                    COUNT(DISTINCT se.id),
                    COUNT(DISTINCT se.id),
                    COUNT(DISTINCT COALESCE(a.sale_id, se.sale_id)),
                    0,
                    COUNT(DISTINCT CASE
                        WHEN se.mapping_status = 'pending_confirmation' THEN se.id
                    END),
                    'completed',
                    MIN(se.imported_at)
                FROM settlement_entries se
                LEFT JOIN settlement_entry_allocations a
                    ON a.settlement_entry_id = se.id
                LEFT JOIN sales s
                    ON s.id = COALESCE(a.sale_id, se.sale_id)
                WHERE se.import_batch_id IS NULL
                GROUP BY se.source_file
                """
            )
        )

        await connection.execute(
            text(
                """
                UPDATE settlement_entries se
                JOIN sales_import_batches batch
                  ON batch.file_name = COALESCE(se.source_file, '历史导入')
                 AND batch.status = 'completed'
                SET se.import_batch_id = batch.id
                WHERE se.import_batch_id IS NULL
                """
            )
        )

        await connection.execute(
            text(
                """
                UPDATE sales_import_batches batch
                LEFT JOIN (
                    SELECT import_batch_id, COUNT(*) AS pending_count
                    FROM settlement_entries
                    WHERE mapping_status = 'pending_confirmation'
                      AND import_batch_id IS NOT NULL
                    GROUP BY import_batch_id
                ) pending ON pending.import_batch_id = batch.id
                SET batch.pending_confirmation = COALESCE(pending.pending_count, 0)
                WHERE batch.status = 'completed'
                """
            )
        )


if __name__ == "__main__":
    async def run_migration() -> None:
        try:
            await migrate()
        finally:
            await engine.dispose()

    asyncio.run(run_migration())
