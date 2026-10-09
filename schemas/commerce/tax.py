"""Tax-related Pydantic schemas for request/response validation."""

from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from enum import Enum


class Currency(str, Enum):
    # Major currencies
    USD = "USD"  # US Dollar
    EUR = "EUR"  # Euro
    GBP = "GBP"  # British Pound
    JPY = "JPY"  # Japanese Yen
    CHF = "CHF"  # Swiss Franc
    CAD = "CAD"  # Canadian Dollar
    AUD = "AUD"  # Australian Dollar
    NZD = "NZD"  # New Zealand Dollar
    
    # Asian currencies
    CNY = "CNY"  # Chinese Yuan
    HKD = "HKD"  # Hong Kong Dollar
    SGD = "SGD"  # Singapore Dollar
    KRW = "KRW"  # South Korean Won
    INR = "INR"  # Indian Rupee
    THB = "THB"  # Thai Baht
    MYR = "MYR"  # Malaysian Ringgit
    PHP = "PHP"  # Philippine Peso
    IDR = "IDR"  # Indonesian Rupiah
    VND = "VND"  # Vietnamese Dong
    TWD = "TWD"  # Taiwan Dollar
    BDT = "BDT"  # Bangladeshi Taka
    PKR = "PKR"  # Pakistani Rupee
    LKR = "LKR"  # Sri Lankan Rupee
    NPR = "NPR"  # Nepalese Rupee
    BTN = "BTN"  # Bhutanese Ngultrum
    MVR = "MVR"  # Maldivian Rufiyaa
    AFN = "AFN"  # Afghan Afghani
    MMK = "MMK"  # Myanmar Kyat
    KHR = "KHR"  # Cambodian Riel
    LAK = "LAK"  # Lao Kip
    BND = "BND"  # Brunei Dollar
    MNT = "MNT"  # Mongolian Tugrik
    
    # European currencies
    SEK = "SEK"  # Swedish Krona
    NOK = "NOK"  # Norwegian Krone
    DKK = "DKK"  # Danish Krone
    PLN = "PLN"  # Polish Zloty
    CZK = "CZK"  # Czech Koruna
    HUF = "HUF"  # Hungarian Forint
    RON = "RON"  # Romanian Leu
    BGN = "BGN"  # Bulgarian Lev
    HRK = "HRK"  # Croatian Kuna
    RSD = "RSD"  # Serbian Dinar
    BAM = "BAM"  # Bosnia and Herzegovina Convertible Mark
    MKD = "MKD"  # Macedonian Denar
    ALL = "ALL"  # Albanian Lek
    MDL = "MDL"  # Moldovan Leu
    ISK = "ISK"  # Icelandic Krona
    
    # Middle Eastern currencies
    AED = "AED"  # UAE Dirham
    SAR = "SAR"  # Saudi Riyal
    QAR = "QAR"  # Qatari Riyal
    KWD = "KWD"  # Kuwaiti Dinar
    BHD = "BHD"  # Bahraini Dinar
    OMR = "OMR"  # Omani Rial
    JOD = "JOD"  # Jordanian Dinar
    ILS = "ILS"  # Israeli Shekel
    TRY = "TRY"  # Turkish Lira
    IRR = "IRR"  # Iranian Rial
    IQD = "IQD"  # Iraqi Dinar
    SYP = "SYP"  # Syrian Pound
    LBP = "LBP"  # Lebanese Pound
    YER = "YER"  # Yemeni Rial
    
    # African currencies
    ZAR = "ZAR"  # South African Rand
    EGP = "EGP"  # Egyptian Pound
    NGN = "NGN"  # Nigerian Naira
    KES = "KES"  # Kenyan Shilling
    GHS = "GHS"  # Ghanaian Cedi
    MAD = "MAD"  # Moroccan Dirham
    TND = "TND"  # Tunisian Dinar
    DZD = "DZD"  # Algerian Dinar
    LYD = "LYD"  # Libyan Dinar
    SDG = "SDG"  # Sudanese Pound
    ETB = "ETB"  # Ethiopian Birr
    UGX = "UGX"  # Ugandan Shilling
    TZS = "TZS"  # Tanzanian Shilling
    RWF = "RWF"  # Rwandan Franc
    BIF = "BIF"  # Burundian Franc
    DJF = "DJF"  # Djiboutian Franc
    SOS = "SOS"  # Somali Shilling
    ERN = "ERN"  # Eritrean Nakfa
    MWK = "MWK"  # Malawian Kwacha
    ZMW = "ZMW"  # Zambian Kwacha
    BWP = "BWP"  # Botswanan Pula
    NAD = "NAD"  # Namibian Dollar
    SZL = "SZL"  # Swazi Lilangeni
    LSL = "LSL"  # Lesotho Loti
    MGA = "MGA"  # Malagasy Ariary
    MUR = "MUR"  # Mauritian Rupee
    SCR = "SCR"  # Seychellois Rupee
    KMF = "KMF"  # Comorian Franc
    AOA = "AOA"  # Angolan Kwanza
    MZN = "MZN"  # Mozambican Metical
    SLL = "SLL"  # Sierra Leonean Leone
    LRD = "LRD"  # Liberian Dollar
    GNF = "GNF"  # Guinean Franc
    CDF = "CDF"  # Congolese Franc
    XAF = "XAF"  # Central African CFA Franc
    XOF = "XOF"  # West African CFA Franc
    
    # Latin American currencies
    BRL = "BRL"  # Brazilian Real
    MXN = "MXN"  # Mexican Peso
    ARS = "ARS"  # Argentine Peso
    CLP = "CLP"  # Chilean Peso
    COP = "COP"  # Colombian Peso
    PEN = "PEN"  # Peruvian Sol
    UYU = "UYU"  # Uruguayan Peso
    PYG = "PYG"  # Paraguayan Guarani
    BOB = "BOB"  # Bolivian Boliviano
    VES = "VES"  # Venezuelan Bolívar
    GYD = "GYD"  # Guyanese Dollar
    SRD = "SRD"  # Surinamese Dollar
    TTD = "TTD"  # Trinidad and Tobago Dollar
    JMD = "JMD"  # Jamaican Dollar
    BBD = "BBD"  # Barbadian Dollar
    BZD = "BZD"  # Belize Dollar
    XCD = "XCD"  # East Caribbean Dollar
    HTG = "HTG"  # Haitian Gourde
    DOP = "DOP"  # Dominican Peso
    CUP = "CUP"  # Cuban Peso
    GTQ = "GTQ"  # Guatemalan Quetzal
    HNL = "HNL"  # Honduran Lempira
    NIO = "NIO"  # Nicaraguan Córdoba
    CRC = "CRC"  # Costa Rican Colón
    PAB = "PAB"  # Panamanian Balboa
    
    # North American currencies
    AWG = "AWG"  # Aruban Florin
    ANG = "ANG"  # Netherlands Antillean Guilder
    
    # Oceania currencies
    FJD = "FJD"  # Fijian Dollar
    PGK = "PGK"  # Papua New Guinean Kina
    SBD = "SBD"  # Solomon Islands Dollar
    VUV = "VUV"  # Vanuatu Vatu
    WST = "WST"  # Samoan Tala
    TOP = "TOP"  # Tongan Paʻanga
    
    # Eastern European and Central Asian currencies
    RUB = "RUB"  # Russian Ruble
    UAH = "UAH"  # Ukrainian Hryvnia
    BYN = "BYN"  # Belarusian Ruble
    KZT = "KZT"  # Kazakhstani Tenge
    UZS = "UZS"  # Uzbekistani Som
    KGS = "KGS"  # Kyrgyzstani Som
    TJS = "TJS"  # Tajikistani Somoni
    TMT = "TMT"  # Turkmenistani Manat
    AZN = "AZN"  # Azerbaijani Manat
    AMD = "AMD"  # Armenian Dram
    GEL = "GEL"  # Georgian Lari
    
    # Special drawing rights and other
    XDR = "XDR"  # Special Drawing Rights
    XAU = "XAU"  # Gold (troy ounce)
    XAG = "XAG"  # Silver (troy ounce)
    XPT = "XPT"  # Platinum (troy ounce)
    XPD = "XPD"  # Palladium (troy ounce)
    
    # Cryptocurrencies (if supported)
    BTC = "BTC"  # Bitcoin
    ETH = "ETH"  # Ethereum
    USDT = "USDT"  # Tether
    USDC = "USDC"  # USD Coin
    BNB = "BNB"  # Binance Coin
    ADA = "ADA"  # Cardano
    SOL = "SOL"  # Solana
    DOT = "DOT"  # Polkadot
    MATIC = "MATIC"  # Polygon
    AVAX = "AVAX"  # Avalanche


# Admin Tax Rate Management Schemas
class RateCreate(BaseModel):
    country_code: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code")
    country_name: str = Field(..., min_length=1, max_length=100)
    province_code: Optional[str] = Field(None, max_length=10, description="State/Province code")
    province_name: Optional[str] = Field(None, max_length=100)
    tax_rate: float = Field(..., ge=0, le=1, description="Tax rate as decimal (e.g., 0.0725 for 7.25%)")
    tax_name: Optional[str] = Field(None, max_length=50, description="e.g., GST, VAT, Sales Tax")
    is_active: bool = True


class RateUpdate(BaseModel):
    country_name: Optional[str] = Field(None, min_length=1, max_length=100)
    province_name: Optional[str] = Field(None, max_length=100)
    tax_rate: Optional[float] = Field(None, ge=0, le=1)
    tax_name: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None


class RateResponse(BaseModel):
    id: UUID
    country_code: str
    country_name: str
    province_code: Optional[str]
    province_name: Optional[str]
    tax_rate: float
    tax_percentage: float  # Computed: tax_rate * 100
    tax_name: Optional[str]
    is_active: bool
    created_at: str
    updated_at: Optional[str]

    class Config:
        from_attributes = True


class BulkRateUpdate(BaseModel):
    id: UUID
    tax_rate: Optional[float] = Field(None, ge=0, le=1)
    is_active: Optional[bool] = None
    tax_name: Optional[str] = Field(None, max_length=50)
