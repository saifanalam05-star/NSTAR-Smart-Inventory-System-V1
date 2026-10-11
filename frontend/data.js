const inventory = [
    {
        sku: "NST-001",
        product_name: "Cola 24 Pack",
        category: "Beverages",
        quantity: 24,
        minimum_stock: 5
    },

    {
        sku: "NST-002",
        product_name: "Water 24 Pack",
        category: "Beverages",
        quantity: 16,
        minimum_stock: 5
    },

    {
        sku: "NST-003",
        product_name: "Rice 10 kg",
        category: "Grocery",
        quantity: 9,
        minimum_stock: 3
    },

    {
        sku: "NST-004",
        product_name: "Paper Towels",
        category: "Household",
        quantity: 2,
        minimum_stock: 5
    }
];


const transactions = [
    {
        time: "14:31",
        sku: "NST-001",
        product_name: "Cola 24 Pack",
        action: "REMOVE",
        quantity_change: -1,
        new_quantity: 23
    },

    {
        time: "14:28",
        sku: "NST-002",
        product_name: "Water 24 Pack",
        action: "ADD",
        quantity_change: 1,
        new_quantity: 17
    },

    {
        time: "14:25",
        sku: "NST-003",
        product_name: "Rice 10 kg",
        action: "REMOVE",
        quantity_change: -1,
        new_quantity: 9
    }
];