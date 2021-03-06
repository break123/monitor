# -*- coding: utf-8 -*-
import re
import json
from scrapy.spider import BaseSpider
from scrapy.contrib.spiders import CrawlSpider, Rule
from scrapy.selector import Selector
from scrapy.http import Request
from scrapy.contrib.linkextractors.sgml import SgmlLinkExtractor
from scrapy_redis.spiders import RedisSpider, RedisMixin

from monitor.items import ProductItem

import sys
reload(sys)
sys.setdefaultencoding('utf8')


class MonitorSpider(RedisMixin, CrawlSpider):
# class MonitorSpider( CrawlSpider ):
# class MonitorSpider(BaseSpider):
# class MonitorSpider(RedisSpider):

	name = "monitorspider"
	redis_key = 'monitorspider:start_urls'

	allowed_domains = [ "tmall.com", "taobao.com",		# tmall
						"jd.com", "3.cn",				# jd
						"feifei.com",					# feifei
						"yhd.com", "yihaodian.com",		# yihaodian
						"yixun.com",					# yixun
						"amazon.cn" ]					# amazon		

	start_urls = []

	pipeline = [ 'MongoPipeline' ]

	rules = (

		Rule(   SgmlLinkExtractor(  allow=(r'detail.tmall.com'),
		                            restrict_xpaths=("//div[@id='J_ItemList']//p[@class='productTitle']"),
		                            unique=True),
		        callback='parseTmall', ),

		Rule(   SgmlLinkExtractor(  allow=(r'list.tmall.com'),
		                            restrict_xpaths=("//a[@class='ui-page-s-next']"),
		                            unique=True), 
		        follow=True ),

	)


	def set_crawler(self, crawler):

		CrawlSpider.set_crawler(self, crawler)
		RedisMixin.setup_redis(self)


	def parse_start_url(self, response):
		""" Main parse function
		"""
		url = response.url

		if url.find( 'detail.tmall.com' ) > -1:
			return self.parseTmall( response )
		elif url.find( 'jd.com' ) > -1:
			return self.parseJd( response )
		elif url.find( 'feifei.com' ) > -1:
			return self.parseFeifei( response )
		elif url.find( 'yhd.com' ) > -1:
			return self.parseYhd( response )
		elif url.find( 'yixun.com' ) > -1:
			return self.parseYixun( response )	
		elif url.find( 'amazon.cn' ) > -1:
			return self.parseAmazon( response )


	def make_requests_from_url(self, url):

		if url.find( 'yhd.com' ) > -1:
			return Request(url, dont_filter=True, cookies={'provinceId':20})
		elif url.find( 'yixun.com' ) > -1:
			return Request(url, dont_filter=True, cookies={'loc':'6_1001_440000_440100_440106_0', 'wsid':'1001'})
		else:
			return Request(url, dont_filter=True)


	######
	#
	# Tmall parser
	#		

	def parseTmall(self, response):
		""" Tmall parser
		"""

		def _referer():
			referer = response.request.headers.get('Referer')
			if referer and referer.find('list.tmall.com') > -1:
				rto = 'http://list.tmall.com/search_product.htm?'
				resultC = re.compile('[\?&]cat=(\d+)').search( referer )
				if resultC: rto += 'cat=%s' % resultC.group(1)
				resultQ = re.compile('[\?&]q=([^&]+)').search( referer )
				if resultQ: 
					if resultC: rto += '&q=%s' % resultQ.group(1)
					else: rto += 'q=%s' % resultQ.group(1)
				if not 'http://list.tmall.com/search_product.htm?' == rto:
					return rto
			elif not referer and response.url.find('detail.tmall.com') > -1:
				return response.url
			return ''

		sel = Selector(response)
		item = ProductItem()  

		item['source']  = 'tmall'       
		item['name']    = self.get_product_name( sel )  
		item['start_url'] = _referer()
		store = ''.join( sel.xpath('//input[@name="seller_nickname"]/@value').extract() )
		item['tm_store'] = '[%s] %s' % (store[-3:], store) if len(store) > 3 else store

		try:
			# 获取TShop字符串，并对TShop字符串进行JSON标准化处理
			TShop_str = sel.re('TShop\.Setup\(((.|\n)+?)\);')[0]
			# 移除注释，目前只有天猫超市有注释，以逗号开头
			regex = re.compile(',\s*\/\/[^\n]*')
			TShop_str = re.sub(regex, ',', TShop_str)
			TShop = eval( TShop_str, type('Dummy', (dict,), dict(__getitem__=lambda s,n:n))() )      
		except SyntaxError:
			return  

		item['itemId']  = TShop.get('itemDO').get('itemId', '')
		item['url']     = response.url

		initApi_url = TShop.get('initApi')

		yield Request(  initApi_url, 
		                headers={'Referer': 'http://www.google.com.hk/'}, 
		                meta={'item': item}, 
		                dont_filter=True,
		                callback=self.parse_initapi )


	def parse_initapi(self, response):
		""" 处理initApi的链接
		"""
		item = response.meta['item']
		try:
		    initObj = eval( response.body.strip().decode('gbk'), type('Dummy', (dict,), dict(__getitem__=lambda s,n:n))() )
		    priceInfo = initObj.get('defaultModel').get('itemPriceResultDO').get('priceInfo')
		    item['price'] = self.get_default_price(priceInfo)
		    item['tm_moonSellCount'] = initObj.get('defaultModel').get('sellCountDO').get('sellCount', 0)
		except:
		    print response.body
		finally:
		    yield Request( 'http://dsr.rate.tmall.com/list_dsr_info.htm?itemId=' + item['itemId'],
		                    meta={'item': item},
		                    dont_filter=True,
		                    callback=self.parse_comment )


	def parse_comment(self, response):
		""" 处理获取评论数的链接
		"""
		item = response.meta['item']
		comment = re.findall('rateTotal\":(\d+)', response.body)[0]
		item['comment'] = int(comment) if comment.isdigit() else 0
		yield item


	def get_product_name(self, sel):
		""" 获取商品名
		"""
		name_node = sel.xpath('//div[@id="J_DetailMeta"]//h3')

		if len(name_node.xpath('./a')) > 0:
		    return name_node.xpath('./a/text()').extract()[0]
		elif len(name_node.xpath('./a')) == 0:
		    return name_node.xpath('./text()').extract()[0]
		else:
		    return ''


	def get_default_price(self, priceInfo):
		""" 计算商品的默认价格
		"""
		def_obj = priceInfo.get('def', None)

		if def_obj:
		    # 有Def属性
		    promotionList = def_obj.get('promotionList', None)
		    if type(promotionList) == list and len(promotionList) > 0:
		        # 有促销信息
		        min_price = sys.maxint
		        for i in range( len(promotionList) ):
		            if promotionList[i].get('price') and float(promotionList[i].get('price')) < min_price:
		                min_price = float(promotionList[i].get('price'))
		        return min_price
		    else:
		        # 没促销信息
		        return float(def_obj.get('price'))
		else:
		    # 没有def属性
		    for sku in priceInfo:
		        promotionList = priceInfo[sku].get('promotionList', None)
		        if type(promotionList) == list and len(promotionList) > 0:
		            # 有促销信息
		            min_price = sys.maxint
		            for i in range( len(promotionList) ):
		                if promotionList[i].get('price') and float(promotionList[i].get('price')) < min_price:
		                    min_price = float(promotionList[i].get('price'))
		            return min_price
		        else:
		            # 没促销信息
		            return float(priceInfo[sku].get('price'))


	######
	#
	# Jd parser
	#

	def parseJd(self, response):
		""" Jd parser
		"""

		sel = Selector(response)
		item = ProductItem()

		item['source'] = 'jd'
		item['name'] = sel.xpath("//div[@id='name']//h1/text()").extract()[0] 
		item['url'] = response.url
		item['itemId'] = self.getSku( response.url )

		# return item
		yield Request(  'http://p.3.cn/prices/get?skuid=J_' + item['itemId'],  
		                meta={'item': item}, 
		                dont_filter=True,
		                callback=self.parsePrice )


	def parsePrice(self, response):	
		item = response.meta['item']
		rto = json.loads( response.body )[0]
		item['price'] = float(rto.get('p', 0))
		yield Request(  'http://club.jd.com/ProductPageService.aspx?method=GetCommentSummaryBySkuId&referenceId=' + item['itemId'] + '&callback=getCommentCount',  
		                meta={'item': item}, 
		                dont_filter=True,
		                callback=self.parseComment )


	def parseComment(self, response):
		item = response.meta['item']
		regex = re.compile('\{.*\}')
		result = regex.search( response.body )
		if result:			
			rto = json.loads( result.group(0) )
			item['comment'] = int(rto.get('CommentCount', 0))
		else:
			item['comment'] = 0
		return item


	def getSku(self, url):
		regex = re.compile( '\/(\d+)\.htm' )
		result = regex.search( url )
		return result.group(1) if result else ''
			

	######
	#
	# Feifei parser
	#	

	def parseFeifei(self, response):
		""" Feifei parser
		"""
		sel = Selector(response)
		item = ProductItem()

		item['source'] 	= 'feifei'
		item['name'] 	= sel.xpath("//h2[@class='np-intro-title']/text()").extract()[0] 
		item['url'] 	= response.url
		price 			= sel.xpath("//dd[@class='price-m']/text()").extract()[0]
		item['price'] 	= float(price[1:])
		item['category']= '|'.join( sel.xpath("//ul[@class='np-crumbs']//a/text()").extract() )

		return item


	######
	#
	# Yhd parser
	#	

	def parseYhd(self, response):
		""" Yihaodian parser
		"""
		sel = Selector(response)
		item = ProductItem()

		item['source'] = 'yhd'
		item['name'] = sel.xpath("//font[@id='productMainName']/text()").extract()[0]
		item['url'] = response.url

		# get pmId
		regex = re.compile('item\/(\d+)')
		result = regex.search( response.url )
		pmId = result.group(1) if result else 0
	
		yield Request(  'http://e.yhd.com/front-pe/queryNumsByPm.do?pmInfoId=%s' % pmId,  
		                meta={'item': item, 'pmId': pmId}, 
		                callback=self.parse_yhd_comment )

		
	def parse_yhd_comment(self, response):
		item = response.meta['item']
		pmId = response.meta['pmId']
		rto = json.loads( response.body )
		item['comment'] = rto.get('experienceNum', -1)
		yield Request(  'http://busystock.i.yihaodian.com/restful/detail?mcsite=1&provinceId=20&pmId=%s' % pmId,  
		                meta={'item': item}, 
		                callback=self.parse_yhd_price )

			
	def parse_yhd_price(self, response):
		item = response.meta['item']
		rto = json.loads( response.body )
		item['price'] = rto.get('currentPrice', -1)
		return item


	######
	#
	# Yixun parser
	#	

	def parseYixun(self, response):
		""" Yixun parser
		"""
		sel = Selector(response)
		item = ProductItem()

		item['source'] = 'yixun'
		item['name'] = sel.xpath("//div[@class='xbase']//h1[@class='xname']/text()").extract()[0]
		item['url'] = response.url

		price = ''.join( sel.xpath("//div[@class='xbase']//span[@itemprop='price']/text()").extract() )
		lowPrice = ''.join( sel.xpath("//div[@class='xbase']//span[@itemprop='lowPrice']/text()").extract() )
		item['price'] = price or lowPrice
	
		return item


	######
	#
	# Amazon parser
	#	

	def parseAmazon(self, response):
		""" Amazon parser
		"""
		sel = Selector(response)
		item = ProductItem()

		item['source'] = 'amazon'
		item['name'] = ''.join( sel.xpath('//span[@id="btAsinTitle"]/span/text()').extract() ).strip()
		item['url'] = response.url
		price = ''.join( sel.xpath('//b[@class="priceLarge"]/text()').extract() )
		item['price'] = price[2:] if len(price) > 2 else ''
	
		return item