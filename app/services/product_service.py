from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import List, Optional, Dict, Any
from uuid import UUID
from fastapi import UploadFile, HTTPException, status
import logging

from app.models.product import Product, ProductImage, ProductSpecification
from app.schemas.product import ProductCreate, ProductUpdate, ProductFilter
from app.crud import product as product_crud
from app.utils.storage import StorageService
from app.services.notification_service import NotificationService

### Product Service - Business logic for products

logger = logging.getLogger(__name__)

### List products with filtering, sorting, and pagination
class ProductService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage_service = StorageService()
        self.notification_service = NotificationService()
    
    async def list_products(
        self,
        filters: ProductFilter,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        query = select(Product).where(Product.status == 'active')
        
        # Apply filters
        if filters.category_id:
            query = query.where(Product.category_id == filters.category_id)
        
        if filters.vendor_id:
            query = query.where(Product.vendor_id == filters.vendor_id)
        
        if filters.brand_id:
            query = query.where(Product.brand_id == filters.brand_id)
        
        if filters.search:
            search_term = f"%{filters.search}%"
            query = query.where(
                or_(
                    Product.name.ilike(search_term),
                    Product.description.ilike(search_term),
                    Product.sku.ilike(search_term)
                )
            )
        
        if filters.min_price is not None:
            query = query.where(Product.base_price >= filters.min_price)
        
        if filters.max_price is not None:
            query = query.where(Product.base_price <= filters.max_price)
        
        if filters.is_featured is not None:
            query = query.where(Product.is_featured == filters.is_featured)
        
        # Apply sorting
        if filters.sort_by == "price":
            order_col = Product.base_price
        elif filters.sort_by == "rating":
            order_col = Product.rating
        elif filters.sort_by == "sales_count":
            order_col = Product.sales_count
        else:
            order_col = Product.created_at
        
        if filters.sort_order == "asc":
            query = query.order_by(order_col.asc())
        else:
            query = query.order_by(order_col.desc())
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        total = result.scalar()
        
        # Apply pagination
        query = query.offset((page - 1) * page_size).limit(page_size)
        
        # Execute query
        result = await self.db.execute(query)
        products = result.scalars().all()
        
        return {
            "products": products,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }
    
    ### Create a new product
    async def create_product(
        self,
        product_in: ProductCreate,
        vendor_id: UUID
    ) -> Product:

        # Generate slug from name
        slug = self._generate_slug(product_in.name)
        
        # Check if slug exists
        existing = await product_crud.get_by_slug(self.db, slug=slug)
        if existing:
            slug = f"{slug}-{vendor_id.hex[:8]}"
        
        # Create product
        product_data = product_in.dict()
        product_data['vendor_id'] = vendor_id
        product_data['slug'] = slug
        product_data['status'] = 'draft'  # New products start as draft
        
        product = await product_crud.create(self.db, obj_in=product_data)
        
        logger.info(f"Product created: {product.id} by vendor {vendor_id}")
        
        return product
    ### Update product
    async def update_product(
        self,
        product_id: UUID,
        product_in: ProductUpdate
    ) -> Product:
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )
        
        # Update slug if name changed
        update_data = product_in.dict(exclude_unset=True)
        if 'name' in update_data and update_data['name'] != product.name:
            update_data['slug'] = self._generate_slug(update_data['name'])
        
        updated_product = await product_crud.update(
            self.db,
            db_obj=product,
            obj_in=update_data
        )
        
        # Notify if product went live
        if (product.status != 'active' and 
            updated_product.status == 'active'):
            await self._notify_product_published(updated_product)
        
        logger.info(f"Product updated: {product_id}")
        
        return updated_product
    ### Upload product images to cloud storage
    async def upload_product_images(
        self,
        product_id: UUID,
        files: List[UploadFile]
    ) -> List[ProductImage]:
        
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found"
            )
        
        if len(files) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum 10 images allowed per product"
            )
        
        images = []
        for idx, file in enumerate(files):
            # Validate file
            if not file.content_type.startswith('image/'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File {file.filename} is not an image"
                )
            
            # Upload to cloud storage
            image_url = await self.storage_service.upload_file(
                file=file,
                folder=f"products/{product_id}",
                filename=f"image_{idx}_{file.filename}"
            )
            
            # Create ProductImage record
            image_data = {
                "product_id": product_id,
                "image_url": image_url,
                "alt_text": f"{product.name} - Image {idx + 1}",
                "display_order": idx,
                "is_primary": (idx == 0)  # First image is primary
            }
            
            image = ProductImage(**image_data)
            self.db.add(image)
            images.append(image)
        
        await self.db.commit()
        
        logger.info(f"Uploaded {len(images)} images for product {product_id}")
        
        return images
    ### Increment product view count
    async def increment_view_count(self, product_id: UUID):
        
        await product_crud.increment_views(self.db, product_id=product_id)
    ### Check if product is low on stock and notify vendor
    async def check_low_stock(self, product_id: UUID):
        
        product = await product_crud.get(self.db, id=product_id)
        if not product:
            return
        
        if product.quantity <= product.low_stock_threshold:
            await self.notification_service.notify_low_stock(
                vendor_id=product.vendor_id,
                product_id=product_id,
                current_quantity=product.quantity
            )
    ### Generate URL-friendly slug from product name
    def _generate_slug(self, name: str) -> str:        
        import re
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug
    ### Notify vendor when product is published
    async def _notify_product_published(self, product: Product):
        await self.notification_service.notify_product_published(
            vendor_id=product.vendor_id,
            product_id=product.id,
            product_name=product.name
        )