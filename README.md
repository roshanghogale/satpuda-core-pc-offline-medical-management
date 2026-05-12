# Veterinary Management System

A modern Python-based veterinary management system with a user-friendly GUI built using Tkinter and SQLite database.

## Features

### 🏥 Billing System
- Customer management with phone verification for due amounts
- Medicine selection with batch number and expiry date tracking
- Automatic doctor name requirement for scheduled medicines
- Tablet quantity management (total tablets, not strips)
- Previous due amount tracking and validation
- Flexible payment handling with remaining due calculations

### 📦 Purchase Management
- Supplier information management
- Medicine type-specific quantity handling:
  - **Tablets**: Stripes × Tablets per stripe + Free stripes
  - **Other types**: Direct quantity + Free items
- Batch number and expiry date tracking
- Automatic stock updates
- HSN code and GST management

### 📊 Inventory Management
- Real-time stock tracking
- Medicine search and filtering
- Stock status indicators (In Stock, Low Stock, Out of Stock)
- Batch-wise medicine management
- Medicine details with purchase/sales history

### 📈 Sales & Purchase History
- Comprehensive transaction history
- Date range filtering
- Customer/Supplier wise filtering
- Bill/Purchase details viewing
- Summary statistics

### ⚙️ Settings & Configuration
- Pharmacy profile management
- Doctor database management
- Supplier information
- Shelf management system

## Key Business Logic

### Medicine Management
- **Batch-based Storage**: Same medicine name with different batch numbers stored separately
- **Expiry Date Tracking**: Medicines with same name and batch but different expiry dates are separate entries
- **Schedule Validation**: Scheduled medicines require doctor name during billing
- **Tablet Calculation**: For tablets, quantity is calculated as stripes × tablets per stripe

### Customer Due Management
- **Previous Due Detection**: Automatically fetches customer's previous due amount
- **Phone Verification**: Required for customers with existing due amounts
- **Flexible Payments**: Supports partial payments with remaining due tracking

### Stock Management
- **Automatic Updates**: Stock automatically updated on purchase and sales
- **Real-time Tracking**: Current stock displayed in inventory
- **Low Stock Alerts**: Visual indicators for low and out-of-stock items

## Installation & Setup

1. **Prerequisites**: Python 3.6 or higher (includes tkinter and sqlite3)

2. **Download**: Clone or download all Python files to a folder

3. **Install Modern UI (Recommended)**:
   ```bash
   # Option 1: Run the setup script
   python3 install_requirements.py
   
   # Option 2: Install manually
   pip install ttkbootstrap>=1.10.1
   ```
   
   *Note: The application will work with standard tkinter if ttkbootstrap is not installed*

4. **Run**: Execute the main application
   ```bash
   python3 main.py
   ```

5. **First Time Setup**:
   - Go to Settings → Pharmacy Profile to set up your clinic details
   - Add doctors in Settings → Doctors tab
   - Start adding medicines through Purchase module

## File Structure

```
veterinary-management/
├── main.py              # Main application entry point
├── billing.py           # Billing module
├── purchase.py          # Purchase management
├── inventory.py         # Inventory management
├── sales_history.py     # Sales history and reports
├── purchase_history.py  # Purchase history and reports
├── settings.py          # Settings and configuration
├── requirements.txt     # Dependencies (none required)
├── README.md           # This file
└── veterinary.db       # SQLite database (created automatically)
```

## Database Schema

### Core Tables
- **customers**: Customer information and contact details
- **medicines**: Medicine inventory with batch and expiry tracking
- **sales**: Sales transactions with due amount management
- **sales_items**: Individual items in each sale
- **purchases**: Purchase transactions
- **purchase_items**: Individual items in each purchase
- **suppliers**: Supplier information
- **doctors**: Doctor database
- **shelves**: Shelf management
- **pharmacy_profile**: Clinic information

## Usage Guide

### 1. Initial Setup
1. Launch the application
2. Go to Settings and set up your pharmacy profile
3. Add doctors who will prescribe medicines
4. Add suppliers for purchases

### 2. Adding Inventory
1. Use Purchase module to add medicines
2. Select medicine type (Tablet, Syrup, Injection, etc.)
3. Enter quantity based on type:
   - For tablets: Number of stripes and tablets per stripe
   - For others: Direct quantity
4. Add batch number, expiry date, and other details
5. Stock will be automatically updated

### 3. Billing Process
1. Enter customer name and phone
2. System will check for previous due amounts
3. Select medicines from dropdown (filtered by stock)
4. For scheduled medicines, doctor name is mandatory
5. Add quantities (for tablets, enter total tablets needed)
6. Apply discount if any
7. Enter amount paid
8. System calculates total due including previous due
9. Generate bill to complete transaction

### 4. Reports and History
- View sales history with filtering options
- Check purchase history
- Monitor inventory levels
- Track customer due amounts

## Technical Features

- **Modern UI**: Clean, intuitive interface using ttkbootstrap with modern themes
- **Responsive Design**: Professional dark theme with modern styling
- **Data Validation**: Comprehensive input validation and error handling
- **Search & Filter**: Advanced search and filtering capabilities
- **Context Menus**: Right-click menus for quick actions
- **Auto-complete**: Medicine and customer name suggestions
- **Stock Alerts**: Visual indicators for stock levels
- **Transaction Safety**: Database transactions with rollback on errors
- **Cross-Platform**: Works on Windows, macOS, and Linux

## Support

This system is designed for veterinary clinics and pharmacies. All features are implemented according to standard pharmacy management practices with special considerations for veterinary medicine requirements.

For customizations or support, refer to the source code comments and documentation within each module.