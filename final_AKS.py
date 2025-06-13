import os
import json
import re
import time
from datetime import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains
from pydantic import BaseModel
from typing import Optional, Dict, Any, Tuple, List
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import logging
import asyncio
import base64
import fitz  # PyMuPDF
from dotenv import load_dotenv  # Import python-dotenv
from azure.storage.blob import BlobServiceClient
import requests
import traceback

# Load environment variables from .env file
load_dotenv()

# Configuration
CONFIG = {
    'STATIC_DIR': os.path.join(os.getcwd(), "static"),
    'JSON_FILE_PATH': os.path.join(os.getcwd(), "salesforce_data.json"),
    'PORT': int(os.getenv("PORT", 8000)),
    'API_KEY': os.getenv("API_KEY"),
    'HOST_URL': os.getenv("HOST_URL"),
    'POSTMAN_ENDPOINT': os.getenv("POSTMAN_ENDPOINT", "https://c89496b5-c613-41c4-b6f9-ae647d74262b.mock.pstmn.io/screenshot"),
    'BROWSER_TIMEOUT': int(os.getenv("BROWSER_TIMEOUT", 300)),
    'ALLOW_UNAUTHENTICATED_SALESFORCE': os.getenv("ALLOW_UNAUTHENTICATED_SALESFORCE", "false").lower() == "true",
    'SALESFORCE_ENDPOINT': os.getenv("SALESFORCE_ENDPOINT"),
    'SALESFORCE_CLIENT_ID': os.getenv("SALESFORCE_CLIENT_ID"),
    'SALESFORCE_CLIENT_SECRET': os.getenv("SALESFORCE_CLIENT_SECRET"),
    'SALESFORCE_USERNAME': os.getenv("SALESFORCE_USERNAME"),
    'SALESFORCE_PASSWORD': os.getenv("SALESFORCE_PASSWORD"),
    'SALESFORCE_TOKEN': os.getenv("SALESFORCE_TOKEN"),
    'SALESFORCE_CALLBACK_URL': os.getenv("SALESFORCE_CALLBACK_URL"),
    'AZURE_STORAGE_ACCOUNT_NAME': os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "formfillscreenshots"),
    'AZURE_ACCESS_KEY': os.getenv("AZURE_ACCESS_KEY"),
    'AZURE_CONTAINER_NAME': os.getenv("AZURE_CONTAINER_NAME", "payload"),
}

# Validate required environment variables
required_env_vars = [
    'AZURE_STORAGE_ACCOUNT_NAME', 'AZURE_ACCESS_KEY', 'AZURE_CONTAINER_NAME','SALESFORCE_ENDPOINT',
    'SALESFORCE_CLIENT_ID', 'SALESFORCE_CLIENT_SECRET', 'SALESFORCE_USERNAME', 'SALESFORCE_PASSWORD', 'API_KEY','SALESFORCE_TOKEN',
    'SALESFORCE_CALLBACK_URL'
]
missing_vars = [var for var in required_env_vars if not CONFIG[var]]
if missing_vars:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")
# Setup
os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Data Models
class ThirdPartyDesignee(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    authorized: Optional[str] = None

class EmployeeDetails(BaseModel):
    other: Optional[str] = None

class LLcDetails(BaseModel):
    number_of_members: Optional[str] = None

class CaseData(BaseModel):
    record_id: str
    form_type: Optional[str] = None
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    formation_date: Optional[str] = None
    business_category: Optional[str] = None
    business_description: Optional[str] = None
    business_address_1: Optional[str] = None
    entity_state: Optional[str] = None
    business_address_2: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    quarter_of_first_payroll: Optional[str] = None
    entity_state_record_state: Optional[str] = None
    case_contact_name: Optional[str] = None
    ssn_decrypted: Optional[str] = None
    proceed_flag: Optional[str] = "true"
    entity_members: Optional[Dict[str, str]] = None
    locations: Optional[List[Dict[str, Any]]] = None
    mailing_address: Optional[Dict[str, str]] = None
    county: Optional[str] = None
    trade_name: Optional[str] = None
    care_of_name: Optional[str] = None
    closing_month: Optional[str] = None
    filing_requirement: Optional[str] = None
    employee_details: Optional[EmployeeDetails] = None
    third_party_designee: Optional[ThirdPartyDesignee] = None
    llc_details: Optional[LLcDetails] = None

# Reusable Form Automation Framework
class FormAutomationBase:
    """Reusable base class for web form automation"""
    
    def __init__(self, headless: bool = False, timeout: int = 10):
        self.timeout = timeout
        self.headless = headless
        self.driver = None
        self.wait = None
    
    def fill_field(self, locator: Tuple[str, str], value: str, label: str = "field"):
        """Fill a form field with error handling"""
        if not value or not value.strip():
            logger.warning(f"Skipping {label} - empty value")
            return False
        
        try:
            field = self.wait.until(EC.element_to_be_clickable(locator))
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
            field.clear()
            field.send_keys(str(value))
            logger.info(f"Filled {label}: {value}")
            return True
        except Exception as e:
            logger.warning(f"Failed to fill {label}: {e}")
            return False
    
    def click_button(self, locator: Tuple[str, str], desc: str = "button", retries: int = 3) -> bool:
        """Click a button with enhanced retry logic and multiple strategies"""
        for attempt in range(retries + 1):
            try:
                element = self.wait.until(EC.presence_of_element_located(locator))
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.5)
                clickable_element = self.wait.until(EC.element_to_be_clickable(locator))
                click_strategies = [
                    lambda: clickable_element.click(),
                    lambda: self.driver.execute_script("arguments[0].click();", clickable_element),
                    lambda: ActionChains(self.driver).move_to_element(clickable_element).click().perform()
                ]
                for strategy in click_strategies:
                    try:
                        strategy()
                        logger.info(f"Clicked {desc}")
                        time.sleep(1)
                        return True
                    except Exception as click_error:
                        if strategy == click_strategies[-1]:
                            raise click_error
                        continue
            except Exception as e:
                if attempt == retries:
                    logger.warning(f"Failed to click {desc} after {retries + 1} attempts: {e}")
                    return False
                logger.warning(f"Click attempt {attempt + 1} failed for {desc}: {e}, retrying...")
                time.sleep(1)
        return False
    
    def select_radio(self, radio_id: str, desc: str = "radio") -> bool:
        """Select radio button with enhanced reliability"""
        try:
            radio = self.wait.until(EC.element_to_be_clickable((By.ID, radio_id)))
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", radio)
            if self.driver.execute_script(f"document.getElementById('{radio_id}').checked = true; return document.getElementById('{radio_id}').checked;"):
                logger.info(f"Selected {desc} via JavaScript")
                return True
            radio.click()
            logger.info(f"Selected {desc} via click")
            return True
        except Exception as e:
            logger.warning(f"Failed to select {desc} (ID: {radio_id}): {e}")
            return False
    
    def select_dropdown(self, locator: Tuple[str, str], value: str, label: str = "dropdown") -> bool:
        """Select dropdown option"""
        try:
            element = self.wait.until(EC.element_to_be_clickable(locator))
            select = Select(element)
            select.select_by_value(value)
            logger.info(f"Selected {label}: {value}")
            return True
        except Exception as e:
            logger.warning(f"Failed to select {label}: {e}")
            return False
    
    def capture_page_as_png(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Capture page as PNG using direct screenshot method or PDF conversion
        """
        try:
            # Method 1: Direct screenshot (most reliable on Windows)
            logger.info("Attempting direct screenshot capture")
            
            # Ensure filename has .png extension
            if not filename.lower().endswith('.png'):
                filename = f"{os.path.splitext(filename)[0]}.png"
                
            png_path = os.path.join(CONFIG['STATIC_DIR'], filename)
            os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
            
            # Take screenshot directly
            screenshot = self.driver.get_screenshot_as_png()
            
            with open(png_path, 'wb') as f:
                f.write(screenshot)
            
            png_url = f"{CONFIG['HOST_URL']}/static/{filename}"
            logger.info(f"PNG saved via screenshot: {png_path}")
            
            return png_path, png_url
            
        except Exception as screenshot_error:
            logger.warning(f"Direct screenshot failed: {screenshot_error}")
            
            # Method 2: PDF conversion with Windows-compatible approach
            pdf_path = None
            try:
                logger.info("Attempting PDF to PNG conversion")
                pdf_data = self.driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True, 
                    "preferCSSPageSize": True,
                    "marginTop": 0, 
                    "marginBottom": 0, 
                    "marginLeft": 0, 
                    "marginRight": 0,
                    "paperWidth": 8.27, 
                    "paperHeight": 11.69, 
                    "landscape": False
                })
                
                # Create temporary PDF
                base_name = os.path.splitext(filename)[0]
                pdf_filename = f"temp_pdf_{base_name}.pdf"
                pdf_path = os.path.join(os.getcwd(), pdf_filename)
                
                with open(pdf_path, "wb") as f:
                    f.write(base64.b64decode(pdf_data["data"]))
                logger.info(f"PDF saved: {pdf_path}")
                
                # Try Windows-compatible PDF conversion
                png_path = None
                
                # Method 2a: Try PyMuPDF with proper error handling
                try:
                    import fitz
                    logger.info("Attempting PyMuPDF conversion")
                    
                    # For Windows, try the most common working methods
                    pdf_document = None
                    try:
                        pdf_document = fitz.open(pdf_path)
                    except:
                        try:
                            pdf_document = fitz.Document(pdf_path)
                        except:
                            try:
                                with open(pdf_path, 'rb') as pdf_file:
                                    pdf_data = pdf_file.read()
                                    pdf_document = fitz.open(stream=pdf_data)
                            except:
                                pass
                    
                    if pdf_document:
                        page = pdf_document[0]  # Get first page
                        
                        # Create high-resolution pixmap
                        mat = fitz.Matrix(2.0, 2.0)  # 2x zoom
                        pix = page.get_pixmap(matrix=mat)
                        
                        if not filename.lower().endswith('.png'):
                            filename = f"{os.path.splitext(filename)[0]}.png"
                        
                        png_path = os.path.join(CONFIG['STATIC_DIR'], filename)
                        os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
                        
                        # Save PNG
                        pix.save(png_path)
                        pdf_document.close()
                        logger.info("PyMuPDF conversion successful")
                    else:
                        raise Exception("Could not open PDF with PyMuPDF")
                        
                except Exception as pymupdf_error:
                    logger.warning(f"PyMuPDF failed: {pymupdf_error}")
                    png_path = None
                
                # Method 2b: Try Pillow with wand (ImageMagick Python binding)
                if png_path is None:
                    try:
                        from wand.image import Image as WandImage
                        logger.info("Attempting conversion with Wand")
                        
                        if not filename.lower().endswith('.png'):
                            filename = f"{os.path.splitext(filename)[0]}.png"
                        
                        png_path = os.path.join(CONFIG['STATIC_DIR'], filename)
                        os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
                        
                        with WandImage(filename=pdf_path, resolution=300) as img:
                            img.format = 'png'
                            img.save(filename=png_path)
                        
                        logger.info("Wand conversion successful")
                        
                    except Exception as wand_error:
                        logger.warning(f"Wand conversion failed: {wand_error}")
                        png_path = None
                
                # Method 2c: Try pdf2image with custom poppler path
                if png_path is None:
                    try:
                        from pdf2image import convert_from_path
                        logger.info("Attempting pdf2image with custom settings")
                        
                        # Try with different poppler configurations
                        poppler_paths = [
                            None,  # Default path
                            r"C:\Program Files\poppler\bin",
                            r"C:\poppler\bin",
                            r".\poppler\bin"
                        ]
                        
                        images = None
                        for poppler_path in poppler_paths:
                            try:
                                if poppler_path:
                                    images = convert_from_path(pdf_path, dpi=300, 
                                                            poppler_path=poppler_path,
                                                            first_page=1, last_page=1)
                                else:
                                    images = convert_from_path(pdf_path, dpi=300,
                                                            first_page=1, last_page=1)
                                break
                            except Exception:
                                continue
                        
                        if images:
                            if not filename.lower().endswith('.png'):
                                filename = f"{os.path.splitext(filename)[0]}.png"
                            
                            png_path = os.path.join(CONFIG['STATIC_DIR'], filename)
                            os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
                            
                            images[0].save(png_path, 'PNG', quality=95)
                            logger.info("pdf2image conversion successful")
                        else:
                            raise Exception("No images generated")
                            
                    except Exception as pdf2image_error:
                        logger.warning(f"pdf2image failed: {pdf2image_error}")
                        png_path = None
                
                if png_path and os.path.exists(png_path):
                    png_url = f"{CONFIG['HOST_URL']}/static/{filename}"
                    logger.info(f"PNG saved: {png_path}")
                    return png_path, png_url
                else:
                    raise Exception("All PDF conversion methods failed")
                    
            except Exception as pdf_error:
                logger.error(f"PDF conversion failed: {pdf_error}")
                return None, None
                
            finally:
                # Clean up temporary PDF file
                if pdf_path and os.path.exists(pdf_path):
                    try:
                        os.remove(pdf_path)
                        logger.info(f"Removed temporary PDF: {pdf_path}")
                    except Exception as e:
                        logger.error(f"Failed to remove PDF: {e}")

    def capture_page_as_png(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        pdf_path = None
        try:
            logger.info("Printing page as PDF")
            pdf_data = self.driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True, 
                "preferCSSPageSize": True,
                "marginTop": 0, 
                "marginBottom": 0, 
                "marginLeft": 0, 
                "marginRight": 0,
                "paperWidth": 8.27, 
                "paperHeight": 11.69, 
                "landscape": False
            })
            
            # Ensure filename has .pdf extension for temp file
            base_name = os.path.splitext(filename)[0]
            pdf_filename = f"temp_pdf_{base_name}.pdf"
            pdf_path = os.path.join(os.getcwd(), pdf_filename)
            
            with open(pdf_path, "wb") as f:
                f.write(base64.b64decode(pdf_data["data"]))
            logger.info(f"PDF saved: {pdf_path}")
            
            logger.info("Converting PDF to PNG")
            
            # Ensure PyMuPDF is imported (should be at top of file)
            import fitz
            
            pdf_document = fitz.open(pdf_path)  # fitz.open() is the correct method
            page = pdf_document.load_page(0)
            
            # Higher resolution for better quality
            mat = fitz.Matrix(3.0, 3.0)  # 3x zoom for better quality
            pix = page.get_pixmap(matrix=mat)
            
            # Ensure filename has .png extension
            if not filename.lower().endswith('.png'):
                filename = f"{os.path.splitext(filename)[0]}.png"
                
            png_path = os.path.join(CONFIG['STATIC_DIR'], filename)
            
            # Ensure the static directory exists
            os.makedirs(CONFIG['STATIC_DIR'], exist_ok=True)
            
            pix.save(png_path)
            pdf_document.close()
            
            png_url = f"{CONFIG['HOST_URL']}/static/{filename}"
            logger.info(f"PNG saved: {png_path}")
            
            return png_path, png_url
            
        except Exception as e:
            logger.error(f"Failed to capture PNG: {e}")
            return None, None
            
        finally:
            # Clean up temporary PDF file
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    logger.info(f"Removed temporary PDF: {pdf_path}")
                except Exception as e:
                    logger.error(f"Failed to remove PDF: {e}")

    def cleanup(self):
        """Clean up browser resources"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Browser closed")
            except Exception as e:
                logger.error(f"Error closing browser: {e}")

# EIN-specific automation class
class IRSEINAutomation(FormAutomationBase):
    STATE_MAPPING = {
        "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", 
        "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
        "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
        "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
        "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
        "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
        "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
        "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
        "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
        "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
        "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
        "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
        "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC"
    }
    
    ENTITY_TYPE_MAPPING = {
        "Sole Proprietorship": "Sole Proprietor",
        "Individual": "Sole Proprietor",
        "Partnership": "Partnership",
        "Joint venture": "Partnership",
        "Limited Partnership": "Partnership",
        "General Partnership": "Partnership",
        "C-Corporation": "Corporations",
        "S-Corporation": "Corporations",
        "Professional Corporation": "Corporations",
        "Corporation": "Corporations",
        "Non-Profit Corporation": "View Additional Types, Including Tax-Exempt and Governmental Organizations",
        "Limited Liability": "Limited Liability Company (LLC)",
        "Company (LLC)": "Limited Liability Company (LLC)",
        "LLC": "Limited Liability Company (LLC)",
        "Limited Liability Company": "Limited Liability Company (LLC)",
        "Limited Liability Company (LLC)": "Limited Liability Company (LLC)",
        "Professional Limited Liability Company": "Limited Liability Company (LLC)",
        "Limited Liability Partnership": "Partnership",
        "LLP": "Partnership",
        "Professional Limited Liability Company (PLLC)": "Limited Liability Company (LLC)",
        "Association": "View Additional Types, Including Tax-Exempt and Governmental Organizations",
        "Co-Ownership": "Partnership",
        "Doing Business As (DBA)": "Sole Proprietor",
        "Trusteeship": "Trusts"
    }
    
    RADIO_BUTTON_MAPPING = {
        "Sole Proprietor": "sole",
        "Partnership": "partnerships",
        "Corporations": "corporations",
        "Limited Liability Company (LLC)": "limited",
        "Estate": "estate",
        "Trusts": "trusts",
        "View Additional Types, Including Tax-Exempt and Governmental Organizations": "viewadditional"
    }
    
    SUB_TYPE_MAPPING = {
        "Sole Proprietorship": "Sole Proprietor",
        "Individual": "Sole Proprietor",
        "Partnership": "Partnership",
        "Joint venture": "Joint Venture",
        "Limited Partnership": "Partnership",
        "General Partnership": "Partnership",
        "C-Corporation": "Corporation",
        "S-Corporation": "S Corporation",
        "Professional Corporation": "Personal Service Corporation",
        "Corporation": "Corporation",
        "Non-Profit Corporation": "**This is dependent on the business_description**",
        "Limited Liability": "N/A",
        "Limited Liability Company (LLC)": "N/A",
        "LLC": "N/A",
        "Limited Liability Company": "N/A",
        "Professional Limited Liability Company": "N/A",
        "Limited Liability Partnership": "Partnership",
        "LLP": "Partnership",
        "Professional Limited Liability Company (PLLC)": "N/A",
        "Association": "N/A",
        "Co-Ownership": "Partnership",
        "Doing Business As (DBA)": "N/A",
        "Trusteeship": "Irrevocable Trust"
    }
    
    SUB_TYPE_BUTTON_MAPPING = {
        "Sole Proprietor": "sole",
        "Household Employer": "house",
        "Partnership": "parnership",
        "Joint Venture": "joint",
        "Corporation": "corp",
        "S Corporation": "scorp",
        "Personal Service Corporation": "personalservice",
        "Irrevocable Trust": "irrevocable",
        "Non-Profit/Tax-Exempt Organization": "nonprofit",
        "Other": "other_option"
    }
    
    def __init__(self):
        super().__init__(headless=False, timeout=10)
    
    def initialize_driver(self):
        try:
            options = uc.ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--disable-gpu')
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-infobars')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--start-maximized')
            prefs = {
                "profile.default_content_setting_values": {
                    "popups": 2, "notifications": 2, "geolocation": 2,
                },
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "autofill.profile_enabled": False,
                "autofill.credit_card_enabled": False,
                "password_manager_enabled": False,
                "profile.password_dismissed_save_prompt": True
            }
            options.add_experimental_option("prefs", prefs)
            self.driver = uc.Chrome(options=options)
            self.wait = WebDriverWait(self.driver, self.timeout)
            self.driver.execute_script("""
                window.alert = function() { return true; };
                window.confirm = function() { return true; };
                window.prompt = function() { return null; };
                window.open = function() { return null; };
            """)
            logger.info("WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise
    
    def navigate_and_fill_form(self, data: CaseData):
        try:
            self.driver.get("https://sa.www4.irs.gov/modiein/individual/index.jsp")
            logger.info("Navigated to IRS EIN form")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @name='submit' and @value='Begin Application >>']"), "Begin Application"):
                raise Exception("Failed to begin application")
            self.wait.until(EC.presence_of_element_located((By.ID, "individual-leftcontent")))
            entity_type = data.entity_type.strip()
            mapped_type = self.ENTITY_TYPE_MAPPING.get(entity_type)
            radio_id = self.RADIO_BUTTON_MAPPING.get(mapped_type)
            if not self.select_radio(radio_id, f"Entity type: {mapped_type}"):
                raise Exception(f"Failed to select entity type: {mapped_type}")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after entity type selection")
            if mapped_type not in ["Limited Liability Company (LLC)", "Estate"]:
                sub_type = self.SUB_TYPE_MAPPING.get(entity_type, "Other")
                if entity_type == "Non-Profit Corporation":
                    business_desc = (data.business_description or "").lower()
                    nonprofit_keywords = ["non-profit", "nonprofit", "charity", "charitable", "501(c)", "tax-exempt"]
                    sub_type = "Non-Profit/Tax-Exempt Organization" if any(keyword in business_desc for keyword in nonprofit_keywords) else "Other"
                sub_type_radio_id = self.SUB_TYPE_BUTTON_MAPPING.get(sub_type, "other_option")
                if not self.select_radio(sub_type_radio_id, f"Sub-type: {sub_type}"):
                    raise Exception(f"Failed to select sub-type: {sub_type}")
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue sub-type (first click)"):
                    raise Exception("Failed to continue after sub-type selection (first click)")
                time.sleep(0.5)
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue sub-type (second click)"):
                    raise Exception("Failed to continue after sub-type selection (second click)")
            else:
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after entity type"):
                    raise Exception("Failed to continue after entity type")
            if mapped_type == "Limited Liability Company (LLC)":
                llc_members = 1
                if data.llc_details and data.llc_details.number_of_members is not None:
                    try:
                        llc_members = int(data.llc_details.number_of_members)
                        if llc_members < 1:
                            llc_members = 1
                    except (ValueError, TypeError):
                        pass
                field = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@id='numbermem' or @name='numbermem']")))
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
                field.clear()
                time.sleep(0.2)
                field.send_keys(str(llc_members))
                state_value = self.normalize_state(data.entity_state or data.entity_state_record_state)
                if not self.select_dropdown((By.ID, "state"), state_value, "State"):
                    raise Exception(f"Failed to select state: {state_value}")
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                    raise Exception("Failed to continue after LLC members and state")
            specific_states = {"AZ", "CA", "ID", "LA", "NV", "NM", "TX", "WA", "WI"}
            if mapped_type == "Limited Liability Company (LLC)" and state_value in specific_states:
                if not self.select_radio("radio_n", "Non-partnership LLC option"):
                    raise Exception("Failed to select non-partnership LLC option")
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after radio_n"):
                    raise Exception("Failed to continue after non-partnership LLC option")
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after confirmation"):
                    raise Exception("Failed to continue after confirmation")
            else:
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after LLC"):
                    raise Exception("Failed to continue after LLC")
            if not self.select_radio("newbiz", "New Business"):
                raise Exception("Failed to select new business")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after business purpose")
            defaults = self._get_defaults(data)
            first_name = data.entity_members.get("first_name_1", defaults["first_name"]) if data.entity_members else defaults["first_name"]
            last_name = data.entity_members.get("last_name_1", defaults["last_name"]) if data.entity_members else defaults["last_name"]
            # Try responsibleParty fields first, fallback to applicant fields
            first_name_filled = self.fill_field((By.ID, "responsiblePartyFirstName"), first_name, "First Name")
            if not first_name_filled:
                first_name_filled = self.fill_field((By.ID, "applicantFirstName"), first_name, "First Name (Applicant)")
            if not first_name_filled:
                raise Exception(f"Failed to fill First Name: {first_name}")
                
            last_name_filled = self.fill_field((By.ID, "responsiblePartyLastName"), last_name, "Last Name")
            if not last_name_filled:
                last_name_filled = self.fill_field((By.ID, "applicantLastName"), last_name, "Last Name (Applicant)")
            if not last_name_filled:
                raise Exception(f"Failed to fill Last Name: {last_name}")
                
            ssn = defaults["ssn_decrypted"].replace("-", "")
            ssn_first_filled = self.fill_field((By.ID, "responsiblePartySSN3"), ssn[:3], "SSN First 3")
            if not ssn_first_filled:
                ssn_first_filled = self.fill_field((By.ID, "applicantSSN3"), ssn[:3], "SSN First 3 (Applicant)")
            if not ssn_first_filled:
                raise Exception("Failed to fill SSN First 3")
                
            ssn_middle_filled = self.fill_field((By.ID, "responsiblePartySSN2"), ssn[3:5], "SSN Middle 2")
            if not ssn_middle_filled:
                ssn_middle_filled = self.fill_field((By.ID, "applicantSSN2"), ssn[3:5], "SSN Middle 2 (Applicant)")
            if not ssn_middle_filled:
                raise Exception("Failed to fill SSN Middle 2")
                
            ssn_last_filled = self.fill_field((By.ID, "responsiblePartySSN4"), ssn[5:], "SSN Last 4")
            if not ssn_last_filled:
                ssn_last_filled = self.fill_field((By.ID, "applicantSSN4"), ssn[5:], "SSN Last 4 (Applicant)")
            if not ssn_last_filled:
                raise Exception("Failed to fill SSN Last 4")
            if not self.select_radio("iamsole", "I Am Sole"):
                raise Exception("Failed to select I Am Sole")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after responsible party")
            if not self.fill_field((By.ID, "physicalAddressStreet"), defaults["business_address_1"], "Physical Street"):
                raise Exception("Failed to fill Physical Street")
            if not self.fill_field((By.ID, "physicalAddressCity"), defaults["city"], "Physical City"):
                raise Exception("Failed to fill Physical City")
            if not self.select_dropdown((By.ID, "physicalAddressState"), self.normalize_state(data.entity_state), "Physical State"):
                raise Exception("Failed to select Physical State")
            if not self.fill_field((By.ID, "physicalAddressZipCode"), defaults["zip_code"], "Physical Zip"):
                raise Exception("Failed to fill Physical Zip")
            phone = defaults["phone"] or "2812173123"
            phone_clean = re.sub(r'\D', '', phone)
            if len(phone_clean) == 10:
                if not self.fill_field((By.ID, "phoneFirst3"), phone_clean[:3], "Phone First 3"):
                    raise Exception("Failed to fill Phone First 3")
                if not self.fill_field((By.ID, "phoneMiddle3"), phone_clean[3:6], "Phone Middle 3"):
                    raise Exception("Failed to fill Phone Middle 3")
                if not self.fill_field((By.ID, "phoneLast4"), phone_clean[6:10], "Phone Last 4"):
                    raise Exception("Failed to fill Phone Last 4")
            if data.care_of_name:
                try:
                    self.wait.until(EC.presence_of_element_located((By.ID, "physicalAddressCareofName")))
                    if not self.fill_field((By.ID, "physicalAddressCareofName"), data.care_of_name, "Physical Care of Name"):
                        logger.warning("Failed to fill Physical Care of Name, proceeding")
                except Exception as e:
                    logger.info(f"physicalAddressCareofName field not found or not fillable: {e}")
                # Mailing address handling
            mailing_address = data.mailing_address or {}
            # Check if mailing address fields are non-empty
            has_mailing_address = any(
                mailing_address.get(key, "").strip()
                for key in ["mailingStreet", "mailingCity", "mailingState", "mailingZip"]
            )
            
            if has_mailing_address:
                if not self.select_radio("radioAnotherAddress_y", "Address option (Yes)"):
                    raise Exception("Failed to select Address option (Yes)")
            else:
                if not self.select_radio("radioAnotherAddress_n", "Address option (No)"):
                    raise Exception("Failed to select Address option (No)")
            
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after address option"):
                raise Exception("Failed to continue after address option")
            
            try:
                if not self.click_button((By.XPATH, "//input[@type='submit' and @name='Submit' and @value='Accept As Entered']"), "Accept As Entered"):
                    raise Exception("Failed to click Accept As Entered")
            except Exception as e:
                logger.info(f"Accept As Entered button not found or not clickable, proceeding: {e}")
            
            if has_mailing_address:
                if not self.fill_field((By.ID, "mailingAddressStreet"), mailing_address.get("mailingStreet", ""), "Mailing Street"):
                    raise Exception("Failed to fill Mailing Street")
                if not self.fill_field((By.ID, "mailingAddressCity"), mailing_address.get("mailingCity", ""), "Mailing City"):
                    raise Exception("Failed to fill Mailing City")
                if not self.fill_field((By.ID, "mailingAddressState"), mailing_address.get("mailingState", ""), "Mailing State"):
                    raise Exception("Failed to select Mailing State")
                if not self.fill_field((By.ID, "mailingAddressPostalCode"), mailing_address.get("mailingZip", ""), "Mailing Zip"):
                    raise Exception("Failed to fill Mailing Zip")
                if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after mailing address"):
                    raise Exception("Failed to continue after mailing address")
                try:
                    if not self.click_button((By.XPATH, "//input[@type='submit' and @name='Submit' and @value='Accept As Entered']"), "Accept As Entered"):
                        raise Exception("Failed to click Accept As Entered")
                except Exception as e:
                    logger.info(f"Accept As Entered button not found or not clickable, proceeding: {e}")
            try:
                business_name = defaults["entity_name"]
                for ending in ['Corp', 'Inc', 'LLC', 'LC', 'PLLC', 'PA']:
                    if business_name.upper().endswith(ending.upper()):
                        business_name = business_name[:-(len(ending))].strip()
                business_name = re.sub(r'[^\w\s\-&]', '', business_name)
            except Exception as e:
                logger.error(f"Failed to process business name: {e}")
                business_name = defaults["entity_name"]  # Fallback to original
            try:
                if not self.fill_field((By.CSS_SELECTOR, "input#businessOperationalLegalName"), business_name, "Legal Business Name"):
                    logger.info("Failed to fill Legal Business Name via CSS selector, ignoring and proceeding")
            except Exception as e:
                logger.info(f"Legal Business Name field not found or not fillable: {e}, ignoring and proceeding")
            if not self.fill_field((By.ID, "businessOperationalCounty"), self.normalize_state(data.entity_state), "County"):
                raise Exception("Failed to fill County")
            try:
                if self.select_dropdown((By.ID, "articalsFiledState"), self.normalize_state(data.county), "Articles Filed State"):
                    logger.info("Successfully selected Articles Filed State with ID 'articalsFiledState'")
                else:
                    logger.info("Failed to select Articles Filed State with ID 'articalsFiledState', trying 'businessOperationalState'")
                    if not self.select_dropdown((By.ID, "businessOperationalState"), self.normalize_state(data.county), "Business Operational State"):
                        logger.info("Failed to select Business Operational State, ignoring and proceeding")
            except Exception as e:
                logger.info(f"Articles Filed State dropdown not found or not selectable: {e}, ignoring and proceeding")
            if data.trade_name:
                if not self.fill_field((By.ID, "businessOperationalTradeName"), data.trade_name, "Trade Name"):
                    raise Exception("Failed to fill Trade Name")
                
            month, year = self.parse_formation_date(defaults["formation_date"])
            if not self.select_dropdown((By.ID, "BUSINESS_OPERATIONAL_MONTH_ID"), str(month), "Formation Month"):
                raise Exception("Failed to select Formation Month")
            if not self.fill_field((By.ID, "BUSINESS_OPERATIONAL_YEAR_ID"), str(year), "Formation Year"):
                raise Exception("Failed to fill Formation Year")
            
            if data.closing_month:
                MONTH_MAPPING = {
                    "january": "JANUARY", "jan": "JANUARY", "1": "JANUARY",
                    "february": "FEBRUARY", "feb": "FEBRUARY", "2": "FEBRUARY",
                    "march": "MARCH", "mar": "MARCH", "3": "MARCH",
                    "april": "APRIL", "apr": "APRIL", "4": "APRIL",
                    "may": "MAY", "5": "MAY",
                    "june": "JUNE", "jun": "JUNE", "6": "JUNE",
                    "july": "JULY", "jul": "JULY", "7": "JULY",
                    "august": "AUGUST", "aug": "AUGUST", "8": "AUGUST",
                    "september": "SEPTEMBER", "sep": "SEPTEMBER", "9": "SEPTEMBER",
                    "october": "OCTOBER", "oct": "OCTOBER", "10": "OCTOBER",
                    "november": "NOVEMBER", "nov": "NOVEMBER", "11": "NOVEMBER",
                    "december": "DECEMBER", "dec": "DECEMBER", "12": "DECEMBER"
                }
                normalized_month = MONTH_MAPPING.get(data.closing_month.lower().strip(), None)
                if normalized_month:
                    retries = 2
                    for attempt in range(1):
                        try:
                            dropdown = self.wait.until(EC.element_to_be_clickable((By.ID, "fiscalMonth")))
                            select = Select(dropdown)
                            available_options = [option.text for option in select.options]
                            if normalized_month not in available_options:
                                logger.warning(f"Fiscal Month {normalized_month} not in available options: {available_options}")
                                break
                            
                            select.select_by_visible_text(normalized_month)
                            logger.info(f"Selected Fiscal Month: {normalized_month}")
                            break
                        except Exception as e:
                            if attempt < retries:
                                logger.warning(f"Attempt {attempt + 1} to select Fiscal Month failed: {e}, retrying...")
                                time.sleep(1)
                            else:
                                logger.error(f"Failed to select Fiscal Month {normalized_month} after {retries + 1} attempts: {e}")
                                break
                else:
                    logger.warning(f"Invalid or unmapped closing_month: {data.closing_month}, skipping fiscal month selection")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after formation date")

            for radio in [
                "radioTrucking_n",
                "radioInvolveGambling_n",
                "radioExciseTax_n",
                "radioSellTobacco_n",
                "radioHasEmployees_n"
            ]:
                if not self.select_radio(radio, radio):
                    raise Exception(f"Failed to select {radio}")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after formation date")
            if not self.select_radio("other", "Other activity"):
                raise Exception("Failed to select Other activity")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after primary activity")
            if not self.select_radio("other", "Other service"):
                raise Exception("Failed to select Other service")
            if not self.fill_field((By.ID, "pleasespecify"), defaults["business_description"], "Business Description"):
                raise Exception("Failed to fill Business Description")
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue"):
                raise Exception("Failed to continue after specify service")
            if not self.select_radio("receiveonline", "Receive Online"):
                raise Exception("Failed to select Receive Online")
            logger.info("Form filled successfully")
        except Exception as e:
            logger.error(f"Form filling failed: {e}")
            raise
    
    def normalize_state(self, state: str) -> str:
        if not state:
            return "TX"
        state_clean = state.upper().strip()
        return self.STATE_MAPPING.get(state_clean, state_clean if len(state_clean) == 2 else "TX")
    
    def parse_formation_date(self, date_str: str) -> Tuple[int, int]:
        if not date_str:
            return 6, 2024
        formats = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]
        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                return parsed.month, parsed.year
            except ValueError:
                continue
        return 6, 2024
    
    def upload_screenshot_to_azure_sync(self, entity_process_id: str, legal_name: str, png_path: str) -> Optional[str]:
        try:
            clean_legal_name = re.sub(r'[^\w\-]', '', legal_name.replace(" ", ""))
            blob_name = f"{entity_process_id}/{clean_legal_name}EINScreenshot.png"
            connection_string = f"DefaultEndpointsProtocol=https;AccountName={CONFIG['AZURE_STORAGE_ACCOUNT_NAME']};AccountKey={CONFIG['AZURE_ACCESS_KEY']};EndpointSuffix=core.windows.net"
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            container_client = blob_service_client.get_container_client(CONFIG['AZURE_CONTAINER_NAME'])
            with open(png_path, "rb") as data:
                container_client.upload_blob(name=blob_name, data=data, overwrite=True)
            blob_url = f"https://{CONFIG['AZURE_STORAGE_ACCOUNT_NAME']}.blob.core.windows.net/{CONFIG['AZURE_CONTAINER_NAME']}/{blob_name}"
            logger.info(f"Screenshot uploaded to Azure Blob Storage: {blob_url}")
            return blob_url
        except Exception as e:
            logger.error(f"Failed to upload screenshot to Azure Blob Storage: {e}")
            return None

    def _save_json_data_sync(self, data: Dict[str, Any]) -> bool:
        try:
            legal_name = data.get('entity_name', 'UnknownEntity')
            clean_legal_name = re.sub(r'[^\w\-]', '', legal_name.replace(" ", ""))
            blob_name = f"{data['record_id']}/{clean_legal_name}_data.json"
            json_data = json.dumps(data, indent=2)
            connection_string = f"DefaultEndpointsProtocol=https;AccountName={CONFIG['AZURE_STORAGE_ACCOUNT_NAME']};AccountKey={CONFIG['AZURE_ACCESS_KEY']};EndpointSuffix=core.windows.net"
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            container_client = blob_service_client.get_container_client(CONFIG['AZURE_CONTAINER_NAME'])
            container_client.upload_blob(
                name=blob_name,
                data=json_data.encode('utf-8'),
                overwrite=True
            )
            blob_url = f"https://{CONFIG['AZURE_STORAGE_ACCOUNT_NAME']}.blob.core.windows.net/{CONFIG['AZURE_CONTAINER_NAME']}/{blob_name}"
            logger.info(f"JSON data uploaded to Azure Blob Storage: {blob_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload JSON data to Azure Blob Storage: {e}")
            return False
        
    async def run_automation(self, data: CaseData) -> Tuple[bool, str, Optional[str], Optional[str]]:
        try:
            missing_fields = []
            for field_name in data.__dict__:
                if getattr(data, field_name) is None and field_name != "record_id":
                    missing_fields.append(field_name)
            if missing_fields:
                logger.info(f"Missing fields: {', '.join(missing_fields)} - using defaults where applicable")
            json_data = {
                "record_id": data.record_id,
                "form_type": data.form_type,
                "entity_name": data.entity_name,
                "entity_type": data.entity_type,
                "formation_date": data.formation_date,
                "business_category": data.business_category,
                "business_description": data.business_description,
                "business_address_1": data.business_address_1,
                "entity_state": data.entity_state,
                "business_address_2": data.business_address_2,
                "city": data.city,
                "zip_code": data.zip_code,
                "quarter_of_first_payroll": data.quarter_of_first_payroll,
                "entity_state_record_state": data.entity_state_record_state,
                "case_contact_name": data.case_contact_name,
                "ssn_decrypted": data.ssn_decrypted,
                "proceed_flag": data.proceed_flag,
                "entity_members": data.entity_members,
                "locations": data.locations,
                "mailing_address": data.mailing_address,
                "county": data.county,
                "trade_name": data.trade_name,
                "care_of_name": data.care_of_name,
                "closing_month": data.closing_month,
                "filing_requirement": data.filing_requirement,
                "employee_details": data.employee_details.model_dump() if data.employee_details else None,
                "third_party_designee": data.third_party_designee.model_dump() if data.third_party_designee else None,
                "llc_details": data.llc_details.model_dump() if data.llc_details else None,
                "response_status": None
            }
            self.initialize_driver()
            self.navigate_and_fill_form(data)
            if not self.click_button((By.XPATH, "//input[@type='submit' and @value='Continue >>']"), "Continue after receive EIN"):
                raise Exception("Failed to continue after receive EIN selection")
            json_data["response_status"] = "success"
            png_filename = f"print_{data.record_id}_{int(time.time())}.png"
            png_path = self.capture_page_as_png(png_filename)
            azure_blob_url = None
            if png_path:
                azure_blob_url = self.upload_screenshot_to_azure_sync(
                    entity_process_id=data.record_id,
                    legal_name=data.entity_name or "UnknownEntity",
                    png_path=png_path
                )
            self._save_json_data_sync(json_data)
            return True, "Form submitted successfully", png_path, azure_blob_url
        except Exception as e:
            logger.error(f"Automation failed: {e}")
            json_data["response_status"] = "fail"
            self._save_json_data_sync(json_data)
            return False, str(e), None, None
        finally:
            self.cleanup()
    
    def _get_defaults(self, data: CaseData) -> Dict[str, Any]:
        entity_members_dict = data.entity_members or {}
        mailing_address_dict = data.mailing_address or {}
        third_party_designee = data.third_party_designee or ThirdPartyDesignee()
        employee_details = data.employee_details or EmployeeDetails()
        llc_details = data.llc_details or LLcDetails()
        return {
            'first_name': entity_members_dict.get("first_name_1", "") or "Rob",
            'last_name': entity_members_dict.get("last_name_1", "") or "Chuchla",
            'phone': entity_members_dict.get('phone_1', '') or '2812173123',
            'ssn_decrypted': str(data.ssn_decrypted or "123456789"),
            'entity_name': str(data.entity_name or "Lane Four Capital Partners LLC"),
            'business_address_1': str(data.business_address_1 or "3315 Cherry Ln"),
            'city': str(data.city or "Austin"),
            'zip_code': str(data.zip_code or "78703"),
            'business_description': str(data.business_description or "Any and lawful business"),
            'formation_date': str(data.formation_date or "2024-05-24"),
            'county': str(data.county or "Travis"),
            'trade_name': str(data.trade_name or ""),
            'care_of_name': str(data.care_of_name or ""),
            'mailing_address': mailing_address_dict,
            'closing_month': str(data.closing_month or ""),
            'filing_requirement': str(data.filing_requirement or ""),
            'employee_details': employee_details.model_dump() if hasattr(employee_details, 'model_dump') else {},
            'third_party_details': third_party_designee.model_dump() if third_party_designee and hasattr(third_party_designee, 'model_dump') else {},
            'llc_details': llc_details.model_dump() if llc_details and hasattr(llc_details, 'model_dump') else {}
        }

# DataProcessor (unchanged, included for context)
class DataProcessor:
    @staticmethod
    def map_form_automation_data(form_data: Dict[str, Any]) -> CaseData:
        responsible_party = form_data.get("responsibleParty", {})
        ownership_details = form_data.get("ownershipDetails", [])
        mailing_address = form_data.get("mailingAddress", {})
        physical_address = form_data.get("physicalAddress", {})
        employee_details = form_data.get("employeeDetails", {})
        third_party = form_data.get("thirdPartyDesignee", {})
        llc_details = form_data.get("llcDetails", {})
        entity_type = form_data.get("entityType")
        if not entity_type:
            logger.warning("No entityType provided in payload, using default: Limited Liability Company (LLC)")
            entity_type = "Limited Liability Company (LLC)"
        else:
            entity_type = entity_type.strip()
        entity_members_dict = {}
        responsible_first_name = responsible_party.get("firstName", "").strip()
        responsible_last_name = responsible_party.get("lastName", "").strip()
        for index, member in enumerate(ownership_details, 1):
            member_first_name = member.get("firstName", "").strip()
            member_last_name = member.get("lastName", "").strip()
            if (member_first_name.lower() == responsible_first_name.lower() and 
                member_last_name.lower() == responsible_last_name.lower()):
                entity_members_dict["first_name_1"] = member.get("firstName")
                entity_members_dict["last_name_1"] = member.get("lastName")
                entity_members_dict["phone_1"] = responsible_party.get("phone")
                entity_members_dict["name_1"] = f"{member.get('firstName', '')} {member.get('lastName', '')}".strip()
                entity_members_dict["percent_ownership_1"] = str(member.get("ownershipPercentage")) if member.get("ownershipPercentage") is not None else None
                break
        if not entity_members_dict:
            entity_members_dict["first_name_1"] = responsible_first_name
            entity_members_dict["last_name_1"] = responsible_last_name
            entity_members_dict["phone_1"] = responsible_party.get("phone")
            entity_members_dict["name_1"] = f"{responsible_first_name} {responsible_last_name}".strip()
            entity_members_dict["percent_ownership_ownership_1"] = None
        locations = [{
            "physicalStreet": physical_address.get("physicalStreet"),
            "physicalCity": physical_address.get("physicalCity"),
            "physicalState": physical_address.get("physicalState"),
            "physicalZip": physical_address.get("physicalZip")
        }]
        return CaseData(
            record_id=form_data.get("entityProcessId", "temp_record_id"),
            form_type=form_data.get("formType"),
            entity_name=form_data.get("legalName"),
            entity_type=entity_type,
            formation_date=form_data.get("startDate"),
            business_category=form_data.get("principalLineOfBusiness"),
            business_description=form_data.get("principalActivity"),
            business_address_1=physical_address.get("physicalStreet"),
            entity_state=physical_address.get("physicalState"),
            city=physical_address.get("physicalCity"),
            zip_code=physical_address.get("Zip"),
            quarter_of_first_payroll=form_data.get("firstWagesDate"),
            entity_state_record_state=physical_address.get("physicalState"),
            case_contact_name=None,
            ssn_decrypted=responsible_party.get("ssnOrItinOrEin"),
            proceed_flag="true",
            entity_members=entity_members_dict,
            locations=locations,
            mailing_address={
                "mailingStreet": mailing_address.get("mailingStreet"),
                "mailingCity": mailing_address.get("mailingCity"),
                "mailingState": mailing_address.get("mailingState"),
                "mailingZip": mailing_address.get("mailingZip")
            },
            county=form_data.get("county"),
            trade_name=form_data.get("tradeName"),
            care_of_name=form_data.get("careOfName"),
            closing_month=form_data.get("closingMonth"),
            filing_requirement=form_data.get("filingRequirement"),
            employee_details=EmployeeDetails(other=employee_details.get("other")),
            third_party_designee=ThirdPartyDesignee(
                name=third_party.get("name"),
                phone=third_party.get("phone"),
                fax=third_party.get("fax"),
                authorized=third_party.get("authorized")
            ),
            llc_details=LLcDetails(number_of_members=str(llc_details.get("numberOfMembers"))) if llc_details.get("numberOfMembers") is not None else None
        )

# FastAPI Application
app = FastAPI(title="IRS EIN API", description="Automated IRS EIN form processing", version="2.0.2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=CONFIG['STATIC_DIR']), name="static")

@app.post("/run-irs-ein")
async def run_irs_ein_endpoint(request: Request, authorization: str = Header(None)):
    """Main endpoint for running IRS EIN automation with direct submission"""
    logger.info(f"Received request from: {request.client.host if request.client else 'Unknown'}")
    if authorization != f"Bearer {CONFIG['API_KEY']}":
        raise HTTPException(status_code=401, detail="Invalid API key")
    try:
        data = await request.json()
        logger.info(f"Received payload: {json.dumps(data, indent=2)}")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid payload format - expected JSON object")
        required_fields = ["entityProcessId", "formType"]
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            raise HTTPException(status_code=400, detail=f"Missing required fields: {missing_fields}")
        if data.get("formType") != "EIN":
            raise HTTPException(status_code=400, detail="Invalid formType, must be 'EIN'")
        case_data = DataProcessor.map_form_automation_data(data)
        logger.info(f"Mapped case data for record_id: {case_data.record_id}")
        automation = IRSEINAutomation()
        success, message, png_path, png_url, azure_blob_url = await automation.run_automation(case_data)
        if success:
            return {
                "message": "Form submitted successfully",
                "status": "Submitted",
                "record_id": case_data.record_id,
                "png_url": png_url,
                "azure_blob_url": azure_blob_url
            }
        else:
            raise HTTPException(status_code=500, detail=message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Endpoint error: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/download-screenshot/{record_id}")
async def download_screenshot(record_id: str):
    """Download screenshot for a record"""
    png_files = [f for f in os.listdir(CONFIG['STATIC_DIR']) 
                if f.startswith(f"print_{record_id}_") and f.endswith(".png")]
    if not png_files:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    latest_png = os.path.join(CONFIG['STATIC_DIR'], sorted(png_files)[-1])
    return FileResponse(latest_png, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=CONFIG['PORT'])